from pinecone import Pinecone
from langchain_pinecone import PineconeVectorStore
from app.rag.embeddings import get_embeddings
from app.core.config import settings
from app.core.logging import logger


def get_vector_store(namespace: str = None) -> PineconeVectorStore:
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    index = pc.Index(settings.PINECONE_INDEX_NAME)
    ns = namespace or settings.PINECONE_NAMESPACE

    return PineconeVectorStore(
        index=index,
        embedding=get_embeddings(),
        namespace=ns,
    )


async def similarity_search(
    query: str,
    tenant_id: str,
    k: int = 5,
    namespace: str = None,
) -> list[dict]:
    ns = namespace or f"sis-{tenant_id[:8]}"

    # 1. Pinecone Documents API Search (Primary for Document Schema index)
    try:
        import requests
        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        index_desc = pc.describe_index(settings.PINECONE_INDEX_NAME)
        host = index_desc.host

        url = f"https://{host}/namespaces/{ns}/documents/search"
        headers = {
            "Api-Key": settings.PINECONE_API_KEY,
            "Content-Type": "application/json",
            "X-Pinecone-API-Version": "2026-07",
        }
        payload = {
            "top_k": k,
            "include_fields": ["chunk_text", "title", "source", "page", "doc_type"],
            "score_by": [
                {"type": "embed", "field": "chunk_text", "query": query}
            ],
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            matches = resp.json().get("matches", [])
            results = []
            for m in matches:
                content = m.get("chunk_text") or m.get("text") or ""
                results.append({
                    "content": content,
                    "source": m.get("source") or m.get("title") or "District Policy",
                    "page": m.get("page", 1),
                    "score": round(float(m.get("_score", 1.0)), 4),
                    "doc_type": m.get("doc_type", "policy"),
                })
            if results:
                return results
    except Exception as e_doc:
        logger.warning("Documents API search error, attempting vector store fallback", error=str(e_doc))

    # 2. Vector Store Fallback (for classic vector indexes)
    try:
        store = get_vector_store(namespace=ns)
        docs = store.similarity_search_with_score(query, k=k)

        results = []
        for doc, score in docs:
            results.append({
                "content": doc.page_content,
                "source": doc.metadata.get("source", "Unknown"),
                "page": doc.metadata.get("page", 0),
                "score": round(float(score), 4),
                "doc_type": doc.metadata.get("doc_type", "general"),
            })
        if results:
            return results
    except Exception as e:
        logger.warning("Vector search via store fallback failed", error=str(e))

    return []
