import os
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4
from sqlalchemy import text, select
from app.db.database import AsyncSessionLocal, engine, Base
import app.models  # registers all models
from app.core.logging import logger
from app.core.config import settings

TENANT_ID = UUID("a0000000-0000-0000-0000-000000000001")


async def seed_all():
    """
    Ensures all 37 database tables exist and seeds realistic K-12 demo data
    for Westlake Unified School District if the tables are empty.
    Also ingests test policy into Pinecone.
    """
    logger.info("Initializing database tables and seed data...")

    # 1. Create all tables from SQLAlchemy models
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("All SQLAlchemy tables verified")
    except Exception as e:
        logger.warning("Table creation note", error=str(e))

    # 2. Check if students already exist
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("SELECT count(*) FROM sis_student WHERE tenant_id = :t"), {"t": str(TENANT_ID)})
            count = res.scalar() or 0
            if count > 0:
                logger.info(f"Database already populated with {count} students. Skipping demo seed.")
                await _seed_pinecone()
                return
        except Exception as e:
            logger.warning("Could not check student count, proceeding with seed", error=str(e))

        logger.info("Seeding realistic demo data for Westlake Unified...")

        # 3. Insert School, Academic Year, Grade Levels
        school_id = uuid4()
        year_id = uuid4()

        await db.execute(
            text("""
            INSERT INTO sis_school (id, tenant_id, name, short_name, is_active)
            VALUES (:id, :t, 'Westlake High School', 'WHS', TRUE)
            ON CONFLICT DO NOTHING;
            """),
            {"id": school_id, "t": str(TENANT_ID)}
        )

        await db.execute(
            text("""
            INSERT INTO sis_academic_year (id, tenant_id, name, start_date, end_date, is_current)
            VALUES (:id, :t, '2025-2026', '2025-08-15', '2026-06-15', TRUE)
            ON CONFLICT DO NOTHING;
            """),
            {"id": year_id, "t": str(TENANT_ID)}
        )

        grades = [("Grade 9", "09", 9), ("Grade 10", "10", 10), ("Grade 11", "11", 11), ("Grade 12", "12", 12)]
        grade_map = {}
        for name, short, order in grades:
            gid = uuid4()
            grade_map[short] = gid
            await db.execute(
                text("""
                INSERT INTO sis_grade_level (id, tenant_id, name, short_name, sort_order)
                VALUES (:id, :t, :name, :short, :order)
                ON CONFLICT DO NOTHING;
                """),
                {"id": gid, "t": str(TENANT_ID), "name": name, "short": short, "order": order}
            )

        # 4. Insert 10 Sample Students with Enrollments
        students_data = [
            ("WHS-2025-001", "Emma", "Marie", "Watson", date(2009, 4, 15), "Female", "09"),
            ("WHS-2025-002", "Liam", "James", "Miller", date(2008, 9, 21), "Male", "10"),
            ("WHS-2025-003", "Sophia", "Rose", "Davis", date(2007, 11, 5), "Female", "11"),
            ("WHS-2025-004", "Noah", "Alexander", "Wilson", date(2007, 2, 18), "Male", "11"),
            ("WHS-2025-005", "Ava", "Grace", "Martinez", date(2006, 7, 30), "Female", "12"),
            ("WHS-2025-006", "Lucas", "Benjamin", "Anderson", date(2009, 12, 10), "Male", "09"),
            ("WHS-2025-007", "Mia", "Elizabeth", "Taylor", date(2008, 5, 25), "Female", "10"),
            ("WHS-2025-008", "Ethan", "Daniel", "Thomas", date(2007, 8, 14), "Male", "11"),
            ("WHS-2025-009", "Isabella", "Claire", "Jackson", date(2006, 3, 3), "Female", "12"),
            ("WHS-2025-010", "Oliver", "Samuel", "White", date(2009, 1, 19), "Male", "09"),
        ]

        student_ids = []
        for sno, fn, mn, ln, dob, g, gr in students_data:
            sid = uuid4()
            student_ids.append((sid, fn, ln, gr))
            await db.execute(
                text("""
                INSERT INTO sis_student (id, tenant_id, student_number, first_name, middle_name, last_name, date_of_birth, gender, is_active, is_deleted)
                VALUES (:id, :t, :sno, :fn, :mn, :ln, :dob, :g, TRUE, FALSE)
                ON CONFLICT DO NOTHING;
                """),
                {"id": sid, "t": str(TENANT_ID), "sno": sno, "fn": fn, "mn": mn, "ln": ln, "dob": dob, "g": g}
            )

            await db.execute(
                text("""
                INSERT INTO sis_enrollment (id, tenant_id, student_id, school_id, academic_year_id, grade_level_id, enrollment_date, status)
                VALUES (:id, :t, :sid, :sch, :yr, :gid, '2025-08-20', 'active')
                ON CONFLICT DO NOTHING;
                """),
                {"id": uuid4(), "t": str(TENANT_ID), "sid": sid, "sch": school_id, "yr": year_id, "gid": grade_map[gr]}
            )

        # 5. Insert Attendance Records (past 14 days)
        today = date.today()
        for i in range(1, 11):
            att_date = today - timedelta(days=i)
            if att_date.weekday() >= 5:  # skip weekends
                continue
            for sid, fn, ln, _ in student_ids:
                if "Miller" in ln and i in [2, 3, 5, 7]:
                    status = "absent"
                    reason = "Unexcused personal"
                elif "Wilson" in ln and i in [1, 4, 8]:
                    status = "excused"
                    reason = "Medical appointment"
                elif i == 4 and "White" in ln:
                    status = "tardy"
                    reason = "Late bus"
                else:
                    status = "present"
                    reason = None

                await db.execute(
                    text("""
                    INSERT INTO sis_attendance_daily (id, tenant_id, student_id, school_id, attendance_date, status, excuse_reason, notes)
                    VALUES (:id, :t, :sid, :sch, :dt, :st, :reason, 'Regular session')
                    ON CONFLICT DO NOTHING;
                    """),
                    {
                        "id": uuid4(),
                        "t": str(TENANT_ID),
                        "sid": sid,
                        "sch": school_id,
                        "dt": att_date,
                        "st": status,
                        "reason": reason,
                    }
                )

        # 6. Insert Fiscal Year & Budget
        fy_id = uuid4()
        await db.execute(
            text("""
            INSERT INTO sis_fiscal_year (id, tenant_id, name, start_date, end_date, is_current)
            VALUES (:id, :t, 'FY 2025-2026', '2025-07-01', '2026-06-30', TRUE)
            ON CONFLICT DO NOTHING;
            """),
            {"id": fy_id, "t": str(TENANT_ID)}
        )

        budget_id = uuid4()
        await db.execute(
            text("""
            INSERT INTO sis_budget (id, tenant_id, fiscal_year_id, school_id, name, status, total_allocated, total_spent, total_forecasted)
            VALUES (:id, :t, :fy, :sch, 'Westlake Annual Operational Budget', 'approved', 4500000.00, 2150000.00, 4500000.00)
            ON CONFLICT DO NOTHING;
            """),
            {"id": budget_id, "t": str(TENANT_ID), "fy": fy_id, "sch": school_id}
        )

        # 7. Insert Sample IEP for SpEd
        first_student = student_ids[0][0]
        second_student = student_ids[1][0]
        await db.execute(
            text("""
            INSERT INTO sis_iep (id, tenant_id, student_id, iep_number, status, disability_category, eligibility_date, start_date, end_date, next_review_date, extended_school_year)
            VALUES (:id, :t, :sid, 'IEP-2025-001', 'active', 'specific_learning_disability', :elig, :st, :en, :rev, FALSE)
            ON CONFLICT DO NOTHING;
            """),
            {
                "id": uuid4(),
                "t": str(TENANT_ID),
                "sid": first_student,
                "elig": today - timedelta(days=365),
                "st": today - timedelta(days=180),
                "en": today + timedelta(days=185),
                "rev": today + timedelta(days=15),   # review due in 15 days
            }
        )
        await db.execute(
            text("""
            INSERT INTO sis_iep (id, tenant_id, student_id, iep_number, status, disability_category, eligibility_date, start_date, end_date, next_review_date, extended_school_year)
            VALUES (:id, :t, :sid, 'IEP-2025-002', 'active', 'speech_language_impairment', :elig, :st, :en, :rev, FALSE)
            ON CONFLICT DO NOTHING;
            """),
            {
                "id": uuid4(),
                "t": str(TENANT_ID),
                "sid": second_student,
                "elig": today - timedelta(days=400),
                "st": today - timedelta(days=300),
                "en": today + timedelta(days=65),
                "rev": today - timedelta(days=5),    # review overdue
            }
        )

        await db.commit()
        logger.info("Database demo seed successfully inserted")

    # 8. Ingest policy document into Pinecone
    await _seed_pinecone()


async def _seed_pinecone():
    if not settings.PINECONE_API_KEY:
        logger.warning("PINECONE_API_KEY not configured, skipping vector ingestion.")
        return {"status": "error", "error": "PINECONE_API_KEY not configured"}

    try:
        from app.rag.ingestion import ingest_document
        
        # Check potential file paths
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_policy.txt"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "test_policy.txt"),
            os.path.join(os.path.dirname(__file__), "test_policy.txt"),
            "/app/test_policy.txt",
            "/app/app/test_policy.txt",
        ]
        policy_bytes = None
        for p in candidates:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    policy_bytes = f.read()
                break

        if not policy_bytes:
            policy_text = """WESTLAKE UNIFIED SCHOOL DISTRICT - ATTENDANCE POLICY
Section 1: Daily Attendance Requirements
All students are required to attend school daily. Compulsory education law requires attendance for students aged 6-18.
An absence is considered excused when a parent or guardian notifies the school within 3 days.
Unexcused absences beyond 3 days in a school year trigger a SARB (School Attendance Review Board) referral.

Section 2: IEP Attendance Provisions
Students with active IEPs may have modified attendance requirements as specified in their individualized plan.
SpEd coordinators must review attendance data monthly for all IEP students.
IDEA compliance requires that IEP meetings be held within 30 days of a student accumulating 10 or more absences.

Section 3: Chronic Absenteeism
A student is considered chronically absent when missing 10 percent or more of school days.
Teachers must flag chronic absenteeism in the SIS within 5 school days of the threshold being reached.
District Admin receives automated weekly reports on chronic absenteeism rates by school and grade level."""
            policy_bytes = policy_text.encode("utf-8")

        logger.info("Ingesting Westlake attendance policy into Pinecone index...", index=settings.PINECONE_INDEX_NAME)
        res = await ingest_document(
            file_bytes=policy_bytes,
            filename="Westlake_District_Attendance_Policy.txt",
            tenant_id=str(TENANT_ID),
            doc_type="policy",
            uploaded_by="System",
        )
        logger.info("Pinecone ingestion result", result=res)
        return res
    except Exception as e:
        logger.error("Pinecone auto-ingestion failed", error=str(e))
        return {"status": "error", "error": str(e)}
