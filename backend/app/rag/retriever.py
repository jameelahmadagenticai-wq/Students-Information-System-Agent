from app.rag.vector_store import similarity_search
from app.services.claude_service import call_claude
from app.rag.cache import rag_cache
from app.core.logging import logger
import json


def _filter_diverse_chunks(docs: list[dict], max_results: int = 5, similarity_threshold: float = 0.75) -> list[dict]:
    """
    Two-stage diversity re-ranking:
    Filters out near-duplicate chunks that repeat the exact same text or high Jaccard token overlap,
    ensuring the LLM receives diverse, complementary context across different pages/sections.
    """
    selected = []
    
    for doc in docs:
        content = doc.get("content", "").strip()
        if not content:
            continue
        
        words_current = set(content.lower().split())
        is_duplicate = False
        
        for kept in selected:
            words_kept = set(kept.get("content", "").lower().split())
            intersection = words_current.intersection(words_kept)
            union = words_current.union(words_kept)
            jaccard_sim = len(intersection) / len(union) if union else 0.0
            
            # If two chunks share more than 75% words, skip the lower-ranked one
            if jaccard_sim > similarity_threshold:
                is_duplicate = True
                break
                
        if not is_duplicate:
            selected.append(doc)
            if len(selected) >= max_results:
                break
                
    return selected


async def rag_query(
    query: str,
    tenant_id: str,
    role: str,
    k: int = 5,
) -> dict:
    # Check Semantic & Exact Query Cache before running retrieval & LLM
    cached = rag_cache.get(tenant_id=tenant_id, role=role, query=query)
    if cached:
        return cached

    # Stage 1: Fetch a larger candidate pool (e.g. 2x requested k) for re-ranking
    fetch_k = max(k * 2, 10)
    docs = await similarity_search(query, tenant_id=tenant_id, k=fetch_k)

    if not docs:
        return {
            "answer": "No relevant documents found in the knowledge base for this query.",
            "citations": [],
            "sources_used": 0,
        }

    # Guardrail: Filter out weak/irrelevant matches (Threshold: 0.30 for llama-text-embed-v2)
    MIN_SCORE_THRESHOLD = 0.30
    qualified_docs = [d for d in docs if d.get("score", 0) >= MIN_SCORE_THRESHOLD]

    if not qualified_docs:
        logger.info(
            "RAG Guardrail triggered: Top match score below threshold",
            top_score=docs[0].get("score") if docs else 0,
            threshold=MIN_SCORE_THRESHOLD,
        )
        return {
            "answer": "This topic is not covered in the available district policies or handbooks. Please consult the school administration directly.",
            "citations": [],
            "sources_used": 0,
        }

    # Stage 2: Two-Stage Re-ranking / Diversity filtering
    # Eliminates redundant/overlapping chunks to keep context rich and distinct
    final_docs = _filter_diverse_chunks(qualified_docs, max_results=k)

    # Build context from final diverse docs
    context_parts = []
    for i, doc in enumerate(final_docs, 1):
        context_parts.append(
            f"[Source {i}: {doc['source']}, Page {doc['page']}]\n{doc['content']}"
        )
    context = "\n\n".join(context_parts)

    system = f"""You are a knowledgeable assistant for Datawebify Student Information System.
Answer questions based ONLY on the provided document context.
Always cite your sources using [Source N] notation.
If the answer is not in the context, say so clearly.
User role: {role}"""

    prompt = f"""Question: {query}

Document Context:
{context}

Provide a clear, accurate answer with source citations."""

    try:
        answer = await call_claude(prompt, system=system, max_tokens=1024)

        citations = [
            {
                "source": doc["source"],
                "page": doc["page"],
                "relevance_score": doc["score"],
                "doc_type": doc["doc_type"],
            }
            for doc in final_docs
        ]

        # Save to Semantic Query Cache
        rag_cache.set(
            tenant_id=tenant_id,
            role=role,
            query=query,
            answer=answer,
            citations=citations,
            sources_used=len(final_docs),
        )

        return {
            "answer": answer,
            "citations": citations,
            "sources_used": len(final_docs),
        }

    except Exception as e:
        logger.error("RAG query failed", error=str(e))
        return {
            "answer": f"Error generating answer: {str(e)}",
            "citations": [],
            "sources_used": 0,
        }
