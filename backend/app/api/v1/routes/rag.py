from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from app.rag.ingestion import ingest_document
from app.rag.retriever import rag_query
from app.core.dependencies import get_current_user, require_roles
from app.schemas.auth import TokenPayload

router = APIRouter(prefix="/rag", tags=["RAG"])


class RAGQueryRequest(BaseModel):
    query: str
    k: int = 5


@router.get("/debug-models")
async def debug_models_endpoint():
    import urllib.request
    import json
    from app.core.config import settings
    key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            all_models = [m.get("name") for m in data.get("models", [])]
            embed_models = [
                m.get("name") for m in data.get("models", [])
                if "embedContent" in m.get("supportedGenerationMethods", [])
            ]
            return {
                "total_models": len(all_models),
                "embed_models": embed_models,
                "all_models": all_models,
            }
    except Exception as e:
        return {"error": str(e)}


@router.get("/status")
async def rag_status():
    try:
        from pinecone import Pinecone
        from app.core.config import settings
        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        index = pc.Index(settings.PINECONE_INDEX_NAME)
        stats = index.describe_index_stats()

        ns_dict = {}
        if hasattr(stats, "namespaces") and stats.namespaces:
            for k, v in stats.namespaces.items():
                ns_dict[str(k)] = getattr(v, "vector_count", getattr(v, "count", 0))

        return {
            "status": "connected",
            "index_name": settings.PINECONE_INDEX_NAME,
            "total_vector_count": int(getattr(stats, "total_vector_count", 0)),
            "namespaces": ns_dict,
            "dimension": int(getattr(stats, "dimension", 0)) if getattr(stats, "dimension", None) else None,
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "detail": str(e),
            "traceback": traceback.format_exc(),
        }


@router.get("/seed-policy")
async def seed_policy_endpoint():
    try:
        from app.db.seed_data import _seed_pinecone
        result = await _seed_pinecone()
        return result or {"status": "done"}
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(default="policy"),
    current_user: TokenPayload = Depends(require_roles(
        "SuperAdmin", "DistrictAdmin", "Principal", "SpEdCoordinator"
    )),
):
    allowed = ["application/pdf", "text/plain"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Only PDF and TXT files are supported")

    if file.size and file.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size must be under 10MB")

    contents = await file.read()
    result = await ingest_document(
        file_bytes=contents,
        filename=file.filename,
        tenant_id=current_user.tenant_id,
        doc_type=doc_type,
        uploaded_by=current_user.sub,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@router.post("/query")
async def query_documents(
    payload: RAGQueryRequest,
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await rag_query(
        query=payload.query,
        tenant_id=current_user.tenant_id,
        role=current_user.role,
        k=payload.k,
    )
    return result
