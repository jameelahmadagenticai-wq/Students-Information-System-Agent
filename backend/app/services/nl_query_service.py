"""
C1 — Natural Language Query Handler
Routes plain-English questions to parameterized SQL queries,
executes them safely, and returns AI-summarized results with citations.
"""

import json
import re
from typing import Any, Optional
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.services.claude_service import get_async_llm_client
from app.rag.cache import sql_cache

# ---------------------------------------------------------------------------
# Allowed query domains — maps intent labels to safe table/column whitelist
# ---------------------------------------------------------------------------

QUERY_DOMAINS = {
    "attendance": {
        "tables": ["sis_attendance_daily", "sis_attendance_period", "sis_student", "sis_school"],
        "description": "attendance records, absences, tardies, present/absent counts",
    },
    "students": {
        "tables": ["sis_student", "sis_enrollment", "sis_grade_level"],
        "description": "student profiles, enrollment status, grade levels",
    },
    "grades": {
        "tables": ["sis_grade", "sis_assignment", "sis_assignment_category", "sis_section_final_grade", "sis_section", "sis_student"],
        "description": "grades, GPA, assignments, final grades by section",
    },
    "scheduling": {
        "tables": ["sis_section", "sis_course", "sis_room", "sis_period", "sis_teacher_assignment"],
        "description": "class sections, room assignments, teacher schedules, conflicts",
    },
    "sped": {
        "tables": ["sis_iep", "sis_iep_goal", "sis_student", "sis_school"],
        "description": "IEP records, special education students, compliance deadlines",
    },
    "budget": {
        "tables": ["sis_budget", "sis_budget_line_item", "sis_budget_transaction", "sis_fiscal_year"],
        "description": "budget allocations, expenditures, resource forecasting",
    },
    "communication": {
        "tables": ["sis_announcement", "sis_notification_log"],
        "description": "announcements, parent notifications, communication logs",
    },
}

# ---------------------------------------------------------------------------
# Role-Based Access Control (RBAC) Security Matrix
# ---------------------------------------------------------------------------

ROLE_FORBIDDEN_TABLES = {
    "Parent": {
        "sis_budget", "sis_budget_line_item", "sis_budget_transaction",
        "sis_iep", "sis_iep_goal", "sis_grade", "sis_section_final_grade",
        "sis_teacher_assignment"
    },
    "Teacher": {
        "sis_budget", "sis_budget_line_item", "sis_budget_transaction"
    },
    "SpEdCoordinator": {
        "sis_budget", "sis_budget_line_item", "sis_budget_transaction"
    },
}

# ---------------------------------------------------------------------------
# Intent + SQL generation prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a SQL expert for a Student Information System (SIS) database using PostgreSQL.
Your job is to convert natural language questions into safe, read-only SELECT queries.

RULES:
1. Only generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, TRUNCATE, or ALTER.
2. Always filter by tenant_id using the placeholder :tenant_id.
3. Use table aliases for readability.
4. Limit results to 200 rows maximum unless the user asks for aggregates.
5. For date ranges, use CURRENT_DATE for relative dates (e.g., "last week" = CURRENT_DATE - INTERVAL '7 days').
6. Return ONLY valid JSON. No markdown, no explanations outside JSON.

ROLE ACCESS CONTROL (RBAC RULES):
- SuperAdmin & DistrictAdmin: Full read access to all domain tables including budget.
- Principal: Access to school students, grades, scheduling, sped, attendance. No district-wide central budget transactions.
- Teacher: Can access attendance, grades, scheduling, and student rosters. STRICTLY FORBIDDEN from accessing budget tables (sis_budget, sis_budget_line_item, sis_budget_transaction). If requested, return sql: null and error: "Access Denied: Teachers do not have permission to access financial or budget records."
- SpEdCoordinator: Can access special education (sis_iep, sis_iep_goal) and students. FORBIDDEN from budget tables.
- Parent: Can access general announcements and attendance protocols. STRICTLY FORBIDDEN under FERPA regulations from querying other students' grades, IEPs, or budget tables. If requested, return sql: null and error: "Access Denied: Parents cannot query district-wide student grades, IEP records, or financial data."

AVAILABLE TABLES AND COLUMNS:

sis_student (id, tenant_id, student_number, first_name, middle_name, last_name, date_of_birth, gender, email, is_active, created_at)
  -- NOTE: enrollment_status lives in sis_enrollment, NOT sis_student
sis_enrollment (id, tenant_id, student_id, school_id, academic_year_id, grade_level_id, enrollment_date, withdrawal_date, status)
  -- status values: 'active', 'withdrawn', 'graduated', 'transferred', 'suspended'
sis_grade_level (id, tenant_id, name, short_name, sort_order)
sis_school (id, tenant_id, name, short_name, is_active)
sis_academic_year (id, tenant_id, name, start_date, end_date, is_current)
sis_attendance_daily (id, tenant_id, student_id, school_id, attendance_date, status, excuse_reason, notes, recorded_by, created_at)
  -- status values: 'present', 'absent', 'tardy', 'excused', 'half_day'
sis_attendance_period (id, tenant_id, student_id, school_id, period_id, attendance_date, status, notes, recorded_by)
  -- status values: 'present', 'absent', 'tardy', 'excused'
sis_period (id, tenant_id, school_id, name, short_name, start_time, end_time, sort_order, is_active)
sis_course (id, tenant_id, course_code, name, description, credits, department, is_active)
sis_section (id, tenant_id, school_id, course_id, academic_year_id, period_id, room_id, teacher_id, section_number, is_active)
sis_grade (id, tenant_id, student_id, assignment_id, section_id, points_earned, percentage, letter_grade, is_excused, is_missing, graded_at)
sis_assignment (id, tenant_id, section_id, category_id, name, max_points, due_date, is_published)
sis_section_final_grade (id, tenant_id, student_id, section_id, academic_year_id, final_percentage, letter_grade, gpa_points, credits_earned, is_passing)
sis_iep (id, tenant_id, student_id, status, disability_category, start_date, end_date, next_review_date, eligibility_date, created_at)
  -- status values: 'draft', 'active', 'expired', 'revoked'
sis_iep_goal (id, iep_id, tenant_id, domain, goal_text, status, sequence)
  -- status values: 'not_started', 'in_progress', 'achieved', 'not_achieved', 'discontinued'
sis_budget (id, tenant_id, fiscal_year_id, school_id, name, status, total_allocated, total_spent, total_forecasted)
  -- status values: 'draft', 'approved', 'active', 'closed'
sis_fiscal_year (id, tenant_id, name, start_date, end_date, is_current)
sis_budget_line_item (id, tenant_id, budget_id, category, name, allocated_amount, spent_amount, forecasted_amount)
  -- category values: 'personnel', 'benefits', 'supplies', 'equipment', 'facilities', 'transportation', 'professional_dev', 'technology', 'special_education', 'contracted_services', 'other'
sis_budget_transaction (id, tenant_id, line_item_id, transaction_type, amount, transaction_date, vendor, description)
sis_announcement (id, tenant_id, school_id, title, body, published_at)
sis_notification_log (id, tenant_id, recipient_user_id, channel, status, sent_at)

Respond with this exact JSON structure:
{
  "intent": "<one of: attendance|students|grades|scheduling|sped|budget|communication|unknown>",
  "confidence": <0.0 to 1.0>,
  "sql": "<safe SELECT query with :tenant_id placeholder>",
  "params": {"tenant_id": "__TENANT_ID__"},
  "summary_prompt": "<instruction for summarizing the results in plain English>",
  "error": null
}

If the question cannot be answered with available tables, set sql to null and error to a user-friendly message.
If the question asks for anything destructive, set sql to null and error to "Only read queries are supported."
"""


def _build_user_message(question: str, user_role: str = "SuperAdmin", context: Optional[dict] = None) -> str:
    ctx_str = ""
    if context:
        ctx_str = f"\nContext: {json.dumps(context)}"
    return (
        f"User Role: {user_role}\n"
        f"Question: {question}{ctx_str}\n\n"
        f"Generate the safe SQL query and metadata adhering strictly to the user's role permissions."
    )


# ---------------------------------------------------------------------------
# SQL safety validator (defense in depth + RBAC table enforcement)
# ---------------------------------------------------------------------------

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|EXEC|EXECUTE|"
    r"pg_sleep|pg_read_file|COPY|\\\\|--)\b",
    re.IGNORECASE,
)


def _is_safe_sql(sql: str, user_role: Optional[str] = None) -> tuple[bool, Optional[str]]:
    if FORBIDDEN_KEYWORDS.search(sql):
        return False, "Dangerous or forbidden SQL keywords detected."
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT"):
        return False, "Only SELECT statements are permitted."
    if ":tenant_id" not in sql:
        return False, "SQL must filter by :tenant_id placeholder."

    # RBAC table enforcement
    if user_role and user_role in ROLE_FORBIDDEN_TABLES:
        forbidden_tables = ROLE_FORBIDDEN_TABLES[user_role]
        for table in forbidden_tables:
            if re.search(r"\b" + table + r"\b", sql, re.IGNORECASE):
                return False, f"Access Denied: Your role ({user_role}) does not have permission to query '{table}'."

    return True, None


# ---------------------------------------------------------------------------
# Result summarizer
# ---------------------------------------------------------------------------

async def _summarize_results(
    client: Any,
    question: str,
    rows: list[dict],
    summary_prompt: str,
    row_count: int,
) -> str:
    import asyncio
    if row_count == 0:
        return "No records were found matching your query."

    # Immediate clean formatter for simple count or single aggregate queries
    if row_count == 1 and len(rows[0]) == 1:
        key, val = list(rows[0].items())[0]
        formatted_key = key.replace("_", " ").title()
        return f"{formatted_key}: {val}"

    # Instant clean summary for single student / record queries
    if row_count == 1:
        details = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in rows[0].items() if v is not None and k != "id")
        return f"Found matching record: {details}"

    # Truncate rows for summarization context (keep first 25)
    sample = rows[:25]
    sample_json = json.dumps(sample, default=str)

    summarize_msg = (
        f"Original question: {question}\n\n"
        f"Query returned {row_count} record(s). Sample data:\n{sample_json}\n\n"
        f"Task: {summary_prompt}\n\n"
        f"Write a clear, concise 1-2 sentence summary for a school administrator."
    )

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model="gemini-3.5-flash",
                max_tokens=400,
                messages=[{"role": "user", "content": summarize_msg}],
            ),
            timeout=10.0
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"LLM summarizer fallback: {e}")
        first_items = [
            str(r.get("name") or (f"{r.get('first_name', '')} {r.get('last_name', '')}").strip() or list(r.values())[0])
            for r in rows[:6]
        ]
        first_items = [item for item in first_items if item]
        preview = ", ".join(first_items)
        return f"Found {row_count} records matching your query ({preview})."


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def handle_nl_query(
    question: str,
    tenant_id: str,
    db: AsyncSession,
    user_role: str,
    school_id: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Full NL-to-SQL pipeline:
    1. Check Semantic SQL Cache (saves 2 LLM calls on hit)
    2. Claude/Gemini parses intent and generates parameterized SQL
    3. Safety & RBAC validator checks SQL
    4. Execute query with tenant_id bound (with self-healing retry)
    5. Claude/Gemini summarizes results
    Returns structured response dict.
    """
    started_at = datetime.utcnow()

    # Step 0: Check Semantic NL2SQL Cache
    cached = sql_cache.get(tenant_id=tenant_id, role=user_role, query=question)
    if cached:
        cached_copy = dict(cached)
        cached_copy["duration_ms"] = int((datetime.utcnow() - started_at).total_seconds() * 1000)
        return cached_copy

    client = get_async_llm_client()

    # Step 1: Parse intent and generate SQL
    try:
        parse_response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _build_user_message(question, user_role=user_role, context=context)}
            ],
        )
        raw = parse_response.content[0].text.strip()

        # Clean markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())

        try:
            parsed = json.loads(cleaned)
        except Exception:
            # Fallback: find outer-most JSON object {...}
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
            else:
                raise
    except Exception as exc:
        logger.error(f"NL parse failed: {exc} | Raw LLM output: {locals().get('raw', '')[:300]}")
        return {
            "success": False,
            "question": question,
            "intent": "unknown",
            "sql": None,
            "row_count": 0,
            "rows": [],
            "summary": "I could not understand that question. Please rephrase it.",
            "error": str(exc),
            "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
        }

    # Step 2: Handle unknown / error from Claude
    if parsed.get("error") or not parsed.get("sql"):
        return {
            "success": False,
            "question": question,
            "intent": parsed.get("intent", "unknown"),
            "sql": None,
            "row_count": 0,
            "rows": [],
            "summary": parsed.get("error", "This question could not be answered with available data."),
            "error": parsed.get("error"),
            "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
        }

    sql: str = parsed["sql"]
    intent: str = parsed.get("intent", "unknown")
    summary_prompt: str = parsed.get("summary_prompt", "Summarize the results.")
    confidence: float = parsed.get("confidence", 0.0)

    # Step 3: Safety and RBAC validation
    is_safe, safety_err = _is_safe_sql(sql, user_role=user_role)
    if not is_safe:
        logger.warning(f"Unsafe/unauthorized SQL blocked for tenant {tenant_id} (role: {user_role}): {safety_err} | SQL: {sql[:200]}")
        return {
            "success": False,
            "question": question,
            "intent": intent,
            "sql": None,
            "row_count": 0,
            "rows": [],
            "summary": safety_err or "That query type is not permitted for your role.",
            "error": safety_err or "SQL failed safety validation.",
            "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
        }

    # Step 4: Execute query (with Self-Healing Retry Loop)
    bind_params: dict[str, Any] = {"tenant_id": tenant_id}
    if ":school_id" in sql and school_id:
        bind_params["school_id"] = school_id

    rows = []
    row_count = 0
    execution_error = None

    for attempt in range(2):
        try:
            result = await db.execute(text(sql), bind_params)
            column_names = list(result.keys())
            raw_rows = result.fetchall()
            rows = [dict(zip(column_names, row)) for row in raw_rows]
            row_count = len(rows)
            execution_error = None
            break
        except Exception as exc:
            execution_error = exc
            logger.warning(
                f"SQL execution attempt {attempt + 1} failed: {exc} | SQL: {sql[:300]}"
            )
            if attempt == 0:
                # Self-healing retry: send DB error back to LLM for correction
                try:
                    fix_prompt = (
                        f"Original Question: {question}\n"
                        f"Generated SQL that failed: {sql}\n"
                        f"Database Error: {str(exc)}\n\n"
                        f"The SQL failed with the database error above. "
                        f"Fix the SQL query according to the PostgreSQL schema and return ONLY the corrected JSON:\n"
                        f'{{"sql": "<corrected SELECT query with :tenant_id placeholder>", "summary_prompt": "<summary instructions>"}}'
                    )
                    fix_response = await client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=2048,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": fix_prompt}],
                    )
                    raw_fix = fix_response.content[0].text.strip()
                    cleaned_fix = re.sub(r"^```(?:json)?\s*", "", raw_fix, flags=re.IGNORECASE)
                    cleaned_fix = re.sub(r"\s*```$", "", cleaned_fix.strip())
                    try:
                        parsed_fix = json.loads(cleaned_fix)
                    except Exception:
                        match = re.search(r"\{.*\}", raw_fix, re.DOTALL)
                        parsed_fix = json.loads(match.group(0)) if match else {}

                    new_sql = parsed_fix.get("sql")
                    is_new_safe, _ = _is_safe_sql(new_sql, user_role=user_role) if new_sql else (False, None)
                    if new_sql and is_new_safe:
                        sql = new_sql
                        if parsed_fix.get("summary_prompt"):
                            summary_prompt = parsed_fix["summary_prompt"]
                        logger.info(f"Self-healing generated corrected SQL: {sql[:300]}")
                        if ":school_id" in sql and school_id:
                            bind_params["school_id"] = school_id
                        continue
                except Exception as fix_err:
                    logger.warning(f"Self-healing retry generation failed: {fix_err}")

    if execution_error is not None:
        logger.error(f"SQL execution ultimately failed: {execution_error} | SQL: {sql[:300]}")
        return {
            "success": False,
            "question": question,
            "intent": intent,
            "sql": sql,
            "row_count": 0,
            "rows": [],
            "summary": "The query could not be executed. Please try a different question.",
            "error": str(execution_error),
            "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
        }

    # Step 5: Summarize
    summary = await _summarize_results(client, question, rows, summary_prompt, row_count)

    duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)

    logger.info(
        f"NL query OK | tenant={tenant_id} intent={intent} "
        f"rows={row_count} confidence={confidence:.2f} ms={duration_ms}"
    )

    result = {
        "success": True,
        "question": question,
        "intent": intent,
        "confidence": confidence,
        "sql": sql,
        "row_count": row_count,
        "rows": rows[:100],  # Return max 100 rows to client
        "summary": summary,
        "error": None,
        "duration_ms": duration_ms,
    }

    # Save to Semantic SQL Cache (saves future LLM & DB calls)
    try:
        sql_cache.set(
            tenant_id=tenant_id,
            role=user_role,
            query=question,
            response_payload=result,
        )
    except Exception as c_err:
        logger.warning(f"Failed to cache NL-to-SQL result: {c_err}")

    return result