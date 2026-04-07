"""Lightweight SQLite ALTERs — SQLAlchemy create_all() does not migrate existing tables."""

from sqlalchemy import text

from app.extensions import db


def apply_sqlite_migrations() -> None:
    """Add missing columns and backfill data for SQLite dev DBs."""
    engine = db.engine
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(faculty)")).fetchall()
        col_names = {row[1] for row in rows}

        if "employee_id" not in col_names:
            conn.execute(text("ALTER TABLE faculty ADD COLUMN employee_id VARCHAR(64)"))

        conn.execute(
            text("""
                UPDATE faculty SET employee_id = (
                    SELECT u.username FROM users u WHERE u.id = faculty.user_id
                )
                WHERE faculty.user_id IS NOT NULL
                  AND (faculty.employee_id IS NULL OR TRIM(COALESCE(faculty.employee_id, '')) = '')
            """)
        )

        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_faculty_employee_id "
                    "ON faculty(employee_id) WHERE employee_id IS NOT NULL"
                )
            )
        except Exception:
            pass
