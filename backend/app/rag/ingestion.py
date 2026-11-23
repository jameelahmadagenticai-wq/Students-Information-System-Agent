from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PDFPlumberLoader, PyPDFLoader, TextLoader
from langchain_core.documents import Document
from pinecone import Pinecone
from langchain_pinecone import PineconeVectorStore
from app.rag.embeddings import get_embeddings
from app.core.config import settings
from app.core.logging import logger
import tempfile
import os


def get_text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


async def ingest_document(
    file_bytes: bytes,
    filename: str,
    tenant_id: str,
    doc_type: str = "policy",
    uploaded_by: str = "",
) -> dict:
    namespace = f"sis-{tenant_id[:8]}"
    splitter = get_text_splitter()

    try:
        suffix = ".pdf" if filename.endswith(".pdf") else ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        if filename.endswith(".pdf"):
            try:
                loader = PDFPlumberLoader(tmp_path)
                raw_docs = loader.load()
            except Exception as pdf_err:
                logger.warning("PDFPlumberLoader failed, falling back to PyPDFLoader", error=str(pdf_err))
                loader = PyPDFLoader(tmp_path)
                raw_docs = loader.load()
        else:
            loader = TextLoader(tmp_path, encoding="utf-8")
            raw_docs = loader.load()

        os.unlink(tmp_path)

        for doc in raw_docs:
            doc.metadata.update({
                "source": filename,
                "tenant_id": tenant_id,
                "doc_type": doc_type,
                "uploaded_by": uploaded_by,
            })

        chunks = splitter.split_documents(raw_docs)
        logger.info("Document chunked", filename=filename, chunks=len(chunks))

        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        index = pc.Index(settings.PINECONE_INDEX_NAME)

        try:
            store = PineconeVectorStore(
                index=index,
                embedding=get_embeddings(),
                namespace=namespace,
            )
            store.add_documents(chunks)
        except Exception as emb_err:
            logger.info("Attempting Pinecone Documents API upload", error=str(emb_err))
            import requests
            index_desc = pc.describe_index(settings.PINECONE_INDEX_NAME)
            host = index_desc.host
            url = f"https://{host}/namespaces/{namespace}/documents/upsert"
            headers = {
                "Api-Key": settings.PINECONE_API_KEY,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": "2026-07",
            }
            documents = []
            clean_title = filename.replace("_", " ").replace(".pdf", "").replace(".txt", "").title()
            for i, c in enumerate(chunks):
                page_num = c.metadata.get("page", 0) + 1
                context_header = f"[Document: {clean_title} | Type: {doc_type.title()} | Page: {page_num}]\n"
                enriched_text = context_header + c.page_content

                documents.append({
                    "_id": f"{filename}-{i}",
                    "chunk_text": enriched_text,
                    "title": clean_title,
                })
            doc_resp = requests.post(url, headers=headers, json={"documents": documents}, timeout=30)
            if doc_resp.status_code >= 400:
                # If documents API failed, attempt upsert_records as fallback
                try:
                    records = [
                        {"_id": d["_id"], "chunk_text": d["chunk_text"], "title": d["title"]}
                        for d in documents
                    ]
                    index.upsert_records(namespace=namespace, records=records)
                except Exception:
                    raise Exception(f"Pinecone Documents API ({doc_resp.status_code}): {doc_resp.text}")

        # Invalidate any cached answers for this tenant so new questions use updated knowledge
        try:
            from app.rag.cache import rag_cache
            rag_cache.invalidate_tenant(tenant_id)
        except Exception as cache_err:
            logger.warning("Cache invalidation failed (non-fatal)", error=str(cache_err))

        logger.info("Document ingested successfully", filename=filename, namespace=namespace)
        return {
            "status": "success",
            "filename": filename,
            "chunks": len(chunks),
            "namespace": namespace,
            "doc_type": doc_type,
        }

    except Exception as e:
        logger.error("Ingestion failed", filename=filename, error=str(e))
        return {"status": "error", "filename": filename, "error": str(e)}
