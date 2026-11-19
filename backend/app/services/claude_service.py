from app.core.config import settings
from app.core.logging import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import json


class GeminiContentBlock:
    def __init__(self, text: str):
        self.text = text


class GeminiMessageResponse:
    def __init__(self, text: str):
        self.content = [GeminiContentBlock(text)]


class GeminiMessagesAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def create(
        self,
        messages: list[dict],
        model: str = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str = "",
        **kwargs,
    ):
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

        chosen_model = settings.PRIMARY_LLM if "gemini" in settings.PRIMARY_LLM.lower() else "gemini-2.0-flash"
        llm = ChatGoogleGenerativeAI(
            model=chosen_model,
            google_api_key=self.api_key,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        lc_messages = []
        if system:
            lc_messages.append(SystemMessage(content=system))
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            elif role == "system":
                lc_messages.append(SystemMessage(content=content))

        resp = await llm.ainvoke(lc_messages)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return GeminiMessageResponse(text)


class GeminiAsyncClient:
    def __init__(self, api_key: str):
        self.messages = GeminiMessagesAPI(api_key=api_key)


def get_async_llm_client():
    gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
    if gemini_key:
        return GeminiAsyncClient(api_key=gemini_key)
    if settings.ANTHROPIC_API_KEY:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    raise ValueError("No LLM API key configured. Please set GEMINI_API_KEY or ANTHROPIC_API_KEY in .env")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def call_claude(
    prompt: str,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    try:
        client = get_async_llm_client()
        message = await client.messages.create(
            model=settings.PRIMARY_LLM,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a helpful AI assistant for a Student Information System.",
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        logger.error("LLM API call failed", error=str(e))
        raise


async def summarize_data(
    data: dict | list,
    context: str,
    role: str = "DistrictAdmin",
) -> str:
    system = f"""You are an AI assistant for Datawebify Student Information System.
Summarize the provided data clearly and concisely for a {role}.
Focus on key insights, trends, and any items needing attention.
Use plain language. No markdown headers. Keep it under 200 words."""

    prompt = f"""Context: {context}

Data to summarize:
{json.dumps(data, indent=2, default=str)}

Provide a clear summary with the most important insights."""

    return await call_claude(prompt, system=system, max_tokens=512)


async def nl_to_filters(
    query: str,
    module: str,
    tenant_id: str,
    role: str,
) -> dict:
    system = """You are a query parser for a Student Information System.
Convert natural language queries into structured filter parameters.
Respond ONLY with a valid JSON object. No explanation. No markdown."""

    module_schemas = {
        "students": '{"name": null, "grade_level": null, "enrollment_status": null, "limit": 50}',
        "attendance": '{"student_id": null, "date_from": null, "date_to": null, "status": null, "limit": 50}',
        "gradebook": '{"student_id": null, "subject": null, "grade_min": null, "grade_max": null, "limit": 50}',
        "scheduling": '{"teacher_id": null, "room": null, "period": null, "subject": null, "limit": 50}',
        "sped": '{"student_id": null, "iep_status": null, "deadline_within_days": null, "limit": 50}',
        "budget": '{"category": null, "fiscal_year": null, "amount_min": null, "amount_max": null, "limit": 50}',
    }

    schema = module_schemas.get(module, '{"query": null, "limit": 50}')

    prompt = f"""Module: {module}
User Role: {role}
Natural language query: "{query}"

Convert this into filter parameters matching this schema:
{schema}

Return only a JSON object with the relevant filters filled in. Use null for unused fields."""

    try:
        raw = await call_claude(prompt, system=system, max_tokens=256)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.error("NL to filters failed", error=str(e))
        return {"error": str(e), "limit": 50}


async def generate_compliance_alert(
    record: dict,
    alert_type: str,
    tenant_id: str,
) -> dict:
    system = """You are a compliance officer AI for a Student Information System.
Analyze records for IDEA/FERPA compliance issues.
Respond ONLY with a valid JSON object."""

    prompt = f"""Alert type: {alert_type}
Record data: {json.dumps(record, indent=2, default=str)}

Analyze this record and return a JSON with:
{{
    "severity": "high|medium|low",
    "issue": "brief description of the compliance issue",
    "action_required": "what needs to be done",
    "deadline": "when it must be done by or null",
    "regulation": "which regulation applies"
}}"""

    try:
        raw = await call_claude(prompt, system=system, max_tokens=512)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.error("Compliance alert generation failed", error=str(e))
        return {"severity": "low", "issue": str(e), "action_required": "Review manually"}
