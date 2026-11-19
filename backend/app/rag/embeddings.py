from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_core.embeddings import Embeddings
from app.core.config import settings
from app.core.logging import logger


class RobustGoogleEmbeddings(Embeddings):
    def __init__(self, google_api_key: str):
        self.google_api_key = google_api_key
        self._models_to_try = [
            "models/embedding-001",
            "models/text-embedding-004",
            "embedding-001",
            "text-embedding-004",
        ]
        self._working_emb = None

    def _get_emb(self, model_name: str):
        return GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=self.google_api_key,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._working_emb:
            return self._working_emb.embed_documents(texts)
        last_err = None
        for m in self._models_to_try:
            try:
                logger.info("Attempting Google embedding model", model=m)
                emb = self._get_emb(m)
                res = emb.embed_documents(texts)
                logger.info("Embedding succeeded with model", model=m)
                self._working_emb = emb
                return res
            except Exception as e:
                logger.warning("Embedding model attempt failed", model=m, error=str(e))
                last_err = e
                continue
        raise last_err

    def embed_query(self, text: str) -> list[float]:
        if self._working_emb:
            return self._working_emb.embed_query(text)
        last_err = None
        for m in self._models_to_try:
            try:
                emb = self._get_emb(m)
                res = emb.embed_query(text)
                self._working_emb = emb
                return res
            except Exception as e:
                last_err = e
                continue
        raise last_err


def get_embeddings():
    gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
    if gemini_key:
        return RobustGoogleEmbeddings(google_api_key=gemini_key)
    if settings.OPENAI_API_KEY:
        return OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.OPENAI_API_KEY,
        )
    raise ValueError("No embeddings API key found. Please set GEMINI_API_KEY or OPENAI_API_KEY in .env")
