"""Database migration helpers.

This module is imported by web_app.py during startup. Keep it syntactically simple
and idempotent so local SQLite databases can be upgraded safely.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from core.config import settings
from db import SessionLocal, crud, engine
from db.models import Base


def _add_column_if_missing(conn, table: str, column: str, col_type: str, default=None):
    """Add a column to an existing table when it is missing."""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    existing = [c["name"] for c in inspector.get_columns(table)]
    if column in existing:
        return

    default_clause = f" DEFAULT {default}" if default is not None else ""
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}"))
    print(f"[migrate] added column: {table}.{column}")


def _ensure_default_question_project(db, user_id: int, name: str):
    from db.models import Project

    project = (
        db.query(Project)
        .filter(
            Project.user_id == user_id,
            Project.project_type == "question",
            Project.name == name,
        )
        .first()
    )
    if project:
        changed = False
        if not project.is_default:
            project.is_default = True
            changed = True
        return project, changed

    project = Project(
        user_id=user_id,
        name=name,
        project_type="question",
        description="",
        color="#2563eb",
        icon="database",
        is_default=True,
    )
    db.add(project)
    db.flush()
    return project, True


def _migrate_legacy_questions_for_user(db, user_id: int, project_id: int):
    import hashlib

    from db.models import Question, QuestionTagMapping, UploadBatch

    legacy_questions = (
        db.query(Question)
        .filter(Question.user_id == user_id, Question.project_id == None)
        .all()
    )
    if not legacy_questions:
        return 0, 0

    migrated = 0
    merged = 0
    batch_ids = set()
    default_review_status = crud.VALID_REVIEW_STATUSES[0]

    for q in legacy_questions:
        if q.batch_id:
            batch_ids.add(q.batch_id)

        base_hash = (q.content_hash or "").strip()
        scoped_hash = (
            hashlib.sha256(f"{project_id}:{base_hash}".encode()).hexdigest()
            if base_hash
            else ""
        )

        existing = None
        if base_hash:
            candidates = [base_hash]
            if scoped_hash:
                candidates.append(scoped_hash)
            existing = (
                db.query(Question)
                .filter(
                    Question.user_id == user_id,
                    Question.project_id == project_id,
                    Question.content_hash.in_(candidates),
                )
                .first()
            )

        if existing:
            if (existing.answer is None or str(existing.answer).strip() == "") and q.answer:
                existing.answer = q.answer
            if (
                existing.user_answer is None
                or str(existing.user_answer).strip() == ""
            ) and q.user_answer:
                existing.user_answer = q.user_answer
            if (
                (
                    existing.review_status is None
                    or str(existing.review_status).strip() == ""
                    or existing.review_status == default_review_status
                )
                and q.review_status
                and q.review_status != default_review_status
            ):
                existing.review_status = q.review_status

            old_mappings = (
                db.query(QuestionTagMapping)
                .filter(QuestionTagMapping.question_id == q.id)
                .all()
            )
            if old_mappings:
                existing_tag_ids = {
                    m.tag_id
                    for m in db.query(QuestionTagMapping)
                    .filter(QuestionTagMapping.question_id == existing.id)
                    .all()
                }
                for m in old_mappings:
                    if m.tag_id not in existing_tag_ids:
                        db.add(QuestionTagMapping(question_id=existing.id, tag_id=m.tag_id))
                db.query(QuestionTagMapping).filter(
                    QuestionTagMapping.question_id == q.id
                ).delete()

            db.delete(q)
            merged += 1
            continue

        q.project_id = project_id
        if base_hash and scoped_hash:
            q.content_hash = scoped_hash
        migrated += 1

    if batch_ids:
        (
            db.query(UploadBatch)
            .filter(
                UploadBatch.user_id == user_id,
                UploadBatch.id.in_(list(batch_ids)),
                UploadBatch.project_id == None,
            )
            .update({UploadBatch.project_id: project_id}, synchronize_session=False)
        )

    return migrated, merged


def migrate():
    """Run idempotent startup migrations."""
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        from sqlalchemy import text

        _add_column_if_missing(conn, "users", "session_version", "INTEGER", 0)
        _add_column_if_missing(conn, "users", "display_name", "VARCHAR(50)")
        _add_column_if_missing(conn, "users", "nickname", "VARCHAR(50)")
        _add_column_if_missing(conn, "users", "avatar_path", "TEXT")
        _add_column_if_missing(conn, "users", "avatar_url", "TEXT")
        _add_column_if_missing(conn, "users", "daily_free_quota", "INTEGER", 100)
        _add_column_if_missing(conn, "users", "daily_free_used", "INTEGER", 0)
        _add_column_if_missing(conn, "users", "daily_free_quota_date", "VARCHAR(10)")

        conn.execute(text("CREATE TABLE IF NOT EXISTS device_bindings (id INTEGER PRIMARY KEY, device_uuid VARCHAR(36) NOT NULL UNIQUE, user_id INTEGER NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME, updated_at DATETIME, FOREIGN KEY(user_id) REFERENCES users(id))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_bindings_device_uuid ON device_bindings(device_uuid)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_bindings_user_id ON device_bindings(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_bindings_is_active ON device_bindings(is_active)"))

        conn.execute(text("CREATE TABLE IF NOT EXISTS device_captures (id INTEGER PRIMARY KEY, device_uuid VARCHAR(36) NOT NULL, user_id INTEGER NOT NULL, file_key VARCHAR(32) NOT NULL, original_filename VARCHAR(255) NOT NULL, file_path TEXT NOT NULL, content_type VARCHAR(100), file_size INTEGER NOT NULL DEFAULT 0, created_at DATETIME, FOREIGN KEY(device_uuid) REFERENCES device_bindings(device_uuid), FOREIGN KEY(user_id) REFERENCES users(id))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_captures_device_uuid ON device_captures(device_uuid)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_captures_user_id ON device_captures(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_captures_file_key ON device_captures(file_key)"))

        conn.execute(text("CREATE TABLE IF NOT EXISTS quota_usage_events (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, action_type VARCHAR(32) NOT NULL, amount INTEGER NOT NULL DEFAULT 1, summary VARCHAR(120) NOT NULL DEFAULT '', quota_date VARCHAR(10) NOT NULL, created_at DATETIME, FOREIGN KEY(user_id) REFERENCES users(id))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_user_id ON quota_usage_events(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_action_type ON quota_usage_events(action_type)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_quota_date ON quota_usage_events(quota_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_created_at ON quota_usage_events(created_at)"))

        _add_column_if_missing(conn, "upload_batches", "project_id", "INTEGER")
        _add_column_if_missing(conn, "questions", "project_id", "INTEGER")
        _add_column_if_missing(conn, "questions", "review_due_at", "DATETIME")
        _add_column_if_missing(conn, "questions", "review_last_at", "DATETIME")
        _add_column_if_missing(conn, "questions", "review_interval_days", "INTEGER", 0)
        _add_column_if_missing(conn, "questions", "review_count", "INTEGER", 0)
        _add_column_if_missing(conn, "questions", "ease_factor", "FLOAT", 2.5)
        _add_column_if_missing(conn, "questions", "page_image_ref", "TEXT")
        _add_column_if_missing(conn, "notes", "project_id", "INTEGER")
        _add_column_if_missing(conn, "split_records", "original_images_json", "TEXT")
        _add_column_if_missing(conn, "notes", "review_due_at", "DATETIME")
        _add_column_if_missing(conn, "notes", "review_last_at", "DATETIME")
        _add_column_if_missing(conn, "notes", "review_interval_days", "INTEGER", 0)
        _add_column_if_missing(conn, "notes", "review_count", "INTEGER", 0)
        _add_column_if_missing(conn, "notes", "ease_factor", "FLOAT", 2.5)
        _add_column_if_missing(conn, "projects", "project_type", "VARCHAR(20)", "'question'")
        _add_column_if_missing(conn, "projects", "summary", "VARCHAR(200)", "''")
        _add_column_if_missing(conn, "projects", "public_id", "TEXT")
        _add_column_if_missing(conn, "chat_sessions", "public_id", "TEXT")
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_public_id_unique ON projects(public_id)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_public_id_unique ON chat_sessions(public_id)"))
        conn.commit()

    import uuid

    from db.models import ChatSession, Project

    with SessionLocal() as db:
        project_rows = (
            db.query(Project)
            .filter((Project.public_id == None) | (Project.public_id == ""))
            .all()
        )
        for project in project_rows:
            project.public_id = str(uuid.uuid4())

        rows = (
            db.query(ChatSession)
            .filter((ChatSession.public_id == None) | (ChatSession.public_id == ""))
            .all()
        )
        for session in rows:
            session.public_id = str(uuid.uuid4())
        if project_rows or rows:
            db.commit()

    # 本地单用户部署：仅初始化唯一的本地管理员账号（已存在时跳过）。
    # 这里不再创建 admin@admin.com / admin2@admin.com 等弱口令内置账号。
    default_users = [
        {
            "email": "dreamer-seve@outlook.com",
            "username": "seve",
            "password": "2147483647",
            "is_admin": True,
        },
    ]
    with SessionLocal() as db:
        for user in default_users:
            if not crud.get_user_by_email(db, user["email"]):
                created = crud.create_user(
                    db,
                    email=user["email"],
                    password_hash=generate_password_hash(user["password"]),
                    username=user["username"],
                    is_admin=user["is_admin"],
                )
                created.display_name = user["username"]
                created.daily_free_quota = settings.daily_free_quota
                db.commit()
                print(
                    "[migrate] created local user: "
                    f"{user['username']} ({user['email']})"
                )

    from db.models import User

    with SessionLocal() as db:
        users = db.query(User).all()
        total_users = 0
        total_created_or_updated = 0
        total_migrated = 0
        total_merged = 0
        project_name = "默认错题库"

        for user in users:
            total_users += 1
            project, project_changed = _ensure_default_question_project(
                db, user.id, project_name
            )
            if project_changed:
                total_created_or_updated += 1
            migrated, merged = _migrate_legacy_questions_for_user(
                db, user.id, project.id
            )
            total_migrated += migrated
            total_merged += merged

        if total_created_or_updated or total_migrated or total_merged:
            db.commit()
            print(
                "[migrate] ensured default question projects: "
                f"users={total_users}, projects={total_created_or_updated}, "
                f"migrated={total_migrated}, merged={total_merged}"
            )


def rebuild():
    """Drop all tables and rebuild the local database. Development only."""
    print("[migrate] dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("[migrate] rebuilding tables...")
    migrate()
    print("[migrate] rebuild complete")


if __name__ == "__main__":
    rebuild()
