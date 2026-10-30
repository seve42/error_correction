"""
数据库模块：引擎创建、Session 工厂、初始化函数
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import os
import sys

# 添加 backend 目录到路径以支持导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import settings
from db.models import Base

# 确保数据库目录存在
db_dir = settings.db_path.parent
db_dir.mkdir(parents=True, exist_ok=True)

# 创建引擎
engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)

# 启用 SQLite 外键约束
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# Session 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _migrate_schema():
    """轻量级自动迁移：为已有表补充新列"""
    import sqlite3
    import uuid
    conn = sqlite3.connect(str(settings.db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS device_bindings (
                id INTEGER PRIMARY KEY,
                device_uuid VARCHAR(36) NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_bindings_device_uuid ON device_bindings(device_uuid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_bindings_user_id ON device_bindings(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_bindings_is_active ON device_bindings(is_active)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS device_captures (
                id INTEGER PRIMARY KEY,
                device_uuid VARCHAR(36) NOT NULL,
                user_id INTEGER NOT NULL,
                file_key VARCHAR(32) NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                file_path TEXT NOT NULL,
                content_type VARCHAR(100),
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                FOREIGN KEY(device_uuid) REFERENCES device_bindings(device_uuid),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_captures_device_uuid ON device_captures(device_uuid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_captures_user_id ON device_captures(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_device_captures_file_key ON device_captures(file_key)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS quota_usage_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                action_type VARCHAR(32) NOT NULL,
                amount INTEGER NOT NULL DEFAULT 1,
                summary VARCHAR(120) NOT NULL DEFAULT '',
                quota_date VARCHAR(10) NOT NULL,
                created_at DATETIME,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_user_id ON quota_usage_events(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_action_type ON quota_usage_events(action_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_quota_date ON quota_usage_events(quota_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_quota_usage_events_created_at ON quota_usage_events(created_at)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_model_selections (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                category VARCHAR(20) NOT NULL,
                source VARCHAR(20) NOT NULL,
                provider_id VARCHAR(36) NOT NULL,
                model_name VARCHAR(100) NOT NULL,
                option_id VARCHAR(255) NOT NULL,
                updated_at DATETIME,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, category)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_user_model_selections_user_id ON user_model_selections(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_user_model_selections_category ON user_model_selections(category)")
        conn.commit()
        # 检查 questions 表是否有 answer 列
        cursor.execute("PRAGMA table_info(questions)")
        columns = {row[1] for row in cursor.fetchall()}
        if 'answer' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN answer TEXT")
            conn.commit()
        if 'project_id' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN project_id INTEGER")
            conn.commit()
        if 'page_image_ref' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN page_image_ref TEXT")
            conn.commit()
        review_columns = {
            "review_due_at": "DATETIME",
            "review_last_at": "DATETIME",
            "review_interval_days": "INTEGER DEFAULT 0 NOT NULL",
            "review_count": "INTEGER DEFAULT 0 NOT NULL",
            "ease_factor": "FLOAT DEFAULT 2.5 NOT NULL",
        }
        for column, col_type in review_columns.items():
            if column not in columns:
                cursor.execute(f"ALTER TABLE questions ADD COLUMN {column} {col_type}")
                conn.commit()

        cursor.execute("PRAGMA table_info(upload_batches)")
        batch_columns = {row[1] for row in cursor.fetchall()}
        if 'project_id' not in batch_columns:
            cursor.execute("ALTER TABLE upload_batches ADD COLUMN project_id INTEGER")
            conn.commit()

        cursor.execute("PRAGMA table_info(notes)")
        note_columns = {row[1] for row in cursor.fetchall()}
        if 'project_id' not in note_columns:
            cursor.execute("ALTER TABLE notes ADD COLUMN project_id INTEGER")
            conn.commit()
        for column, col_type in review_columns.items():
            if column not in note_columns:
                cursor.execute(f"ALTER TABLE notes ADD COLUMN {column} {col_type}")
                conn.commit()

        cursor.execute("PRAGMA table_info(projects)")
        project_columns = {row[1] for row in cursor.fetchall()}
        if 'project_type' not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'question'")
            conn.commit()
        if 'summary' not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN summary TEXT DEFAULT ''")
            conn.commit()
        if 'public_id' not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN public_id TEXT")
            conn.commit()

        cursor.execute("SELECT id FROM projects WHERE public_id IS NULL OR public_id = ''")
        missing_project_rows = cursor.fetchall()
        for (project_id,) in missing_project_rows:
            cursor.execute(
                "UPDATE projects SET public_id = ? WHERE id = ?",
                (str(uuid.uuid4()), project_id),
            )
        if missing_project_rows:
            conn.commit()

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_public_id_unique ON projects(public_id)")
        conn.commit()

        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}
        if 'display_name' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            conn.commit()
        if 'nickname' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
            conn.commit()
        if 'avatar_path' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
            conn.commit()
        if 'avatar_url' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
            conn.commit()
        if 'daily_free_quota' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_free_quota INTEGER DEFAULT 100")
            conn.commit()
        if 'daily_free_used' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_free_used INTEGER DEFAULT 0")
            conn.commit()
        if 'daily_free_quota_date' not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_free_quota_date TEXT")
            conn.commit()

        # chat_sessions 表：question_id 需要改为 nullable
        # SQLite 不支持 ALTER COLUMN，需要重建表
        cursor.execute("PRAGMA table_info(split_records)")
        split_record_columns = {row[1] for row in cursor.fetchall()}
        if 'original_images_json' not in split_record_columns:
            cursor.execute("ALTER TABLE split_records ADD COLUMN original_images_json TEXT")
            conn.commit()

        cursor.execute("PRAGMA table_info(chat_sessions)")
        cs_columns = {row[1]: row for row in cursor.fetchall()}
        if 'title' not in cs_columns:
            # 旧表结构，需要重建
            cursor.execute("DROP TABLE IF EXISTS chat_messages")
            cursor.execute("DROP TABLE IF EXISTS chat_sessions")
            conn.commit()

        # chat_sessions.public_id：对外使用 UUID，避免暴露自增主键
        cursor.execute("PRAGMA table_info(chat_sessions)")
        cs_columns = {row[1] for row in cursor.fetchall()}
        if 'public_id' not in cs_columns:
            cursor.execute("ALTER TABLE chat_sessions ADD COLUMN public_id TEXT")
            conn.commit()

        # 回填历史数据的 public_id
        cursor.execute("SELECT id FROM chat_sessions WHERE public_id IS NULL OR public_id = ''")
        missing_rows = cursor.fetchall()
        for (sid,) in missing_rows:
            cursor.execute(
                "UPDATE chat_sessions SET public_id = ? WHERE id = ?",
                (str(uuid.uuid4()), sid),
            )
        if missing_rows:
            conn.commit()

        # 给 public_id 创建唯一索引（SQLite 不支持后加 UNIQUE 约束，用唯一索引替代）
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_public_id_unique ON chat_sessions(public_id)")
        conn.commit()

    finally:
        conn.close()


def init_db():
    """初始化数据库：建表并执行轻量级自动迁移"""
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
