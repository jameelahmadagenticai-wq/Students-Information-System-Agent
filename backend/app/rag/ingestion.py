from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
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
            loader = PyPDFLoader(tmp_path)
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
            for i, c in enumerate(chunks):
                documents.append({
                    "_id": f"{filename}-{i}",
                    "chunk_text": c.page_content,
                    "title": filename,
                })
            doc_resp = requests.post(url, headers=headers, json={"documents": documents}, timeout=30)
            if doc_resp.status_code >= 400:
                # If documents API failed, attempt upsert_records as fallback
                try:
                    records = [
                        {"_id": f"{filename}-{i}", "chunk_text": c.page_content, "title": filename}
                        for i, c in enumerate(chunks)
                    ]
                    index.upsert_records(namespace=namespace, records=records)
                except Exception:
                    raise Exception(f"Pinecone Documents API ({doc_resp.status_code}): {doc_resp.text}")

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
