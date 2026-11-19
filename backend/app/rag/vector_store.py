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
        logger.warning("Vector search via store failed, attempting integrated search_records", error=str(e))

    try:
        import requests
        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        index_desc = pc.describe_index(settings.PINECONE_INDEX_NAME)
        host = index_desc.host

        # 1. Try Documents API search
        url = f"https://{host}/namespaces/{ns}/documents/search"
        headers = {
            "Api-Key": settings.PINECONE_API_KEY,
            "Content-Type": "application/json",
            "X-Pinecone-API-Version": "2026-07",
        }
        resp = requests.post(
            url,
            headers=headers,
            json={"query": {"inputs": {"text": query}, "top_k": k}},
            timeout=10,
        )
        if resp.status_code == 200:
            hits = resp.json().get("result", {}).get("hits", [])
            results = []
            for h in hits:
                fields = h.get("fields", {})
                content = fields.get("chunk_text") or fields.get("text") or ""
                results.append({
                    "content": content,
                    "source": fields.get("source") or fields.get("title") or "District Policy",
                    "page": fields.get("page", 0),
                    "score": round(float(h.get("_score", 1.0)), 4),
                    "doc_type": fields.get("doc_type", "policy"),
                })
            if results:
                return results

        # 2. Try records search fallback
        index = pc.Index(settings.PINECONE_INDEX_NAME)
        search_res = index.search_records(
            namespace=ns,
            query={"inputs": {"text": query}, "top_k": k}
        )
        hits = search_res.get("result", {}).get("hits", [])
        results = []
        for h in hits:
            fields = h.get("fields", {})
            content = fields.get("chunk_text") or fields.get("text") or ""
            results.append({
                "content": content,
                "source": fields.get("source") or fields.get("title") or "District Policy",
                "page": fields.get("page", 0),
                "score": round(float(h.get("_score", 1.0)), 4),
                "doc_type": fields.get("doc_type", "policy"),
            })
        return results
    except Exception as e2:
        logger.error("Pinecone search fallback error", error=str(e2))
        return []
