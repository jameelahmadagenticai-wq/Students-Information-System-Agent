from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.api.v1.router import api_router
import app.models  # noqa: F401 — registers all models with Base.metadata


async def ensure_seed_users():
    from app.db.database import AsyncSessionLocal
    from sqlalchemy import text
    import os

    try:
        schema_path = os.path.join(os.path.dirname(__file__), "db", "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                content = f.read()

            if "-- Seed default users for Westlake Unified" in content:
                seed_sql = content.split("-- Seed default users for Westlake Unified")[1]
                async with AsyncSessionLocal() as session:
                    for stmt in seed_sql.split(";"):
                        s = stmt.strip()
                        if s:
                            await session.execute(text(s))
                    await session.commit()
                logger.info("Default seed users verified")
    except Exception as e:
        logger.warning("Seed user verification note", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(debug=settings.APP_DEBUG)
    logger.info("SIS API starting up", env=settings.APP_ENV)
    await ensure_seed_users()
    try:
        from app.db.seed_data import seed_all
        await seed_all()
    except Exception as e:
        logger.warning("Auto-seed error", error=str(e))
    yield
    logger.info("SIS API shutting down")


app = FastAPI(
    title="SIS API — Westlake Unified School District",
    description=(
        "AI-powered Student Information System built by Jameel Ahmad. "
        "Multi-tenant SaaS platform with LangGraph orchestration, "
        "Google Gemini integration, and Pinecone RAG pipeline."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = [
    "https://sis.datawebify.com",
    "https://sis-api.datawebify.com",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:80",
    "http://localhost",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.up\.railway\.app|https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["System"])
async def root():
    return {
        "service":  "SIS API",
        "version":  "1.0.0",
        "docs":     "/docs",
        "health":   "/api/v1/health",
        "built_by": "Jameel Ahmad",
        "website":  "https://github.com/jameelahmadagenticai-wq/Students-Information-System-Agent",
    }
