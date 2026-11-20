from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger()


def get_primary_llm(temperature: float = 0.0):
    gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
    if gemini_key:
        model_name = settings.PRIMARY_LLM if "gemini" in settings.PRIMARY_LLM.lower() else "gemini-3.1-flash-lite"
        if "2.0" in model_name:
            model_name = "gemini-3.1-flash-lite"
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=gemini_key,
            temperature=temperature,
        )
    if settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.PRIMARY_LLM,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            temperature=temperature,
            max_tokens=4096,
        )
    if settings.OPENAI_API_KEY:
        return ChatOpenAI(
            model=settings.PRIMARY_LLM or "gpt-4o",
            openai_api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=4096,
        )
    raise ValueError("No LLM API key configured. Please set GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY in .env")


def get_fallback_llm(temperature: float = 0.0):
    if settings.OPENAI_API_KEY:
        return ChatOpenAI(
            model=settings.FALLBACK_LLM or "gpt-4o",
            openai_api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=4096,
        )
    return get_primary_llm(temperature)


def get_llm(use_fallback: bool = False, temperature: float = 0.0):
    if use_fallback:
        logger.info("Using fallback LLM", model=settings.FALLBACK_LLM)
        return get_fallback_llm(temperature)
    return get_primary_llm(temperature)
