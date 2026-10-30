"""
数据库 ORM 模型定义
"""

import uuid
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False)
    display_name = Column(String(50), nullable=True)
    nickname = Column(String(50), nullable=True)
    avatar_path = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    session_version = Column(Integer, default=0, nullable=False)
    daily_free_quota = Column(Integer, default=100, nullable=False)
    daily_free_used = Column(Integer, default=0, nullable=False)
    daily_free_quota_date = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    questions = relationship("Question", back_populates="user")
    upload_batches = relationship("UploadBatch", back_populates="user")
    split_records = relationship("SplitRecord", back_populates="user")
    provider_configs = relationship("ProviderConfig", back_populates="user", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="user")
    workflow_runs = relationship("WorkflowRun", back_populates="user")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    device_bindings = relationship("DeviceBinding", back_populates="user")
    device_captures = relationship("DeviceCapture", back_populates="user")
    quota_usage_events = relationship("QuotaUsageEvent", back_populates="user", cascade="all, delete-orphan")
    model_selections = relationship("UserModelSelection", back_populates="user", cascade="all, delete-orphan")


class UserModelSelection(Base):
    """User-level selected LLM model option."""
    __tablename__ = "user_model_selections"
    __table_args__ = (UniqueConstraint("user_id", "category", name="uq_user_model_selection_category"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(20), nullable=False, index=True)
    source = Column(String(20), nullable=False)
    provider_id = Column(String(36), nullable=False)
    model_name = Column(String(100), nullable=False)
    option_id = Column(String(255), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="model_selections")


class QuotaUsageEvent(Base):
    """额度消费事件，用于前端展示今日拆分和最近活动。"""
    __tablename__ = "quota_usage_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String(32), nullable=False, index=True)
    amount = Column(Integer, default=1, nullable=False)
    summary = Column(String(120), default="", nullable=False)
    quota_date = Column(String(10), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="quota_usage_events")


class ModelUsageEvent(Base):
    """One model request's token usage and cost (all providers, billable or not)."""
    __tablename__ = "model_usage_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    provider_source = Column(String(16), default="", nullable=False)
    provider = Column(String(20), default="", nullable=False)
    model_name = Column(String(128), default="", nullable=False, index=True)
    action_type = Column(String(32), default="other", nullable=False, index=True)
    run_id = Column(String(64), nullable=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    cached_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    cost_micro_yuan = Column(Integer, default=0, nullable=False)
    billable = Column(Boolean, default=False, nullable=False)
    quota_date = Column(String(10), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class DeviceBinding(Base):
    """Hardware camera binding owned by a user."""
    __tablename__ = "device_bindings"

    id = Column(Integer, primary_key=True)
    device_uuid = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="device_bindings")
    captures = relationship("DeviceCapture", back_populates="device")


class DeviceCapture(Base):
    """Image uploaded by a bound hardware camera."""
    __tablename__ = "device_captures"

    id = Column(Integer, primary_key=True)
    device_uuid = Column(String(36), ForeignKey("device_bindings.device_uuid"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_key = Column(String(32), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    content_type = Column(String(100), default="")
    file_size = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="device_captures")
    device = relationship("DeviceBinding", back_populates="captures")


class Project(Base):
    """Learning space that groups questions and notes."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    public_id = Column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    project_type = Column(String(20), default="question", nullable=False, index=True)
    summary = Column(String(200), default="")
    description = Column(Text, default="")
    color = Column(String(20), default="#2563eb")
    icon = Column(String(50), default="book-open")
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="projects")
    upload_batches = relationship("UploadBatch", back_populates="project")
    questions = relationship("Question", back_populates="project")
    notes = relationship("Note", back_populates="project")


class ProviderConfig(Base):
    """用户级 API 供应商配置（每用户可配置多个，同类激活一个）"""
    __tablename__ = "provider_configs"

    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(20), nullable=False)  # 'openai' | 'anthropic' | 'paddleocr'
    name = Column(String(100), default="")
    is_active = Column(Boolean, default=False)
    api_key = Column(Text, default="")  # 加密存储（后续）
    base_url = Column(Text, default="")
    model_name = Column(String(100), default="")
    light_model_name = Column(String(100), default="")
    supports_function_calling = Column(Boolean, default=True)
    # PaddleOCR 专用
    use_doc_orientation = Column(Boolean, default=False)
    use_doc_unwarping = Column(Boolean, default=False)
    use_chart_recognition = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="provider_configs")


class SystemProviderConfig(Base):
    """系统级 API 供应商配置（管理员维护，全站共享）"""
    __tablename__ = "system_provider_configs"

    id = Column(String(36), primary_key=True)  # UUID
    category = Column(String(20), nullable=False)  # 'openai' | 'anthropic' | 'paddleocr'
    name = Column(String(100), default="")
    is_active = Column(Boolean, default=False)
    api_key = Column(Text, default="")
    base_url = Column(Text, default="")
    model_name = Column(String(100), default="")
    light_model_name = Column(String(100), default="")
    supports_function_calling = Column(Boolean, default=True)
    # PaddleOCR 专用
    use_doc_orientation = Column(Boolean, default=False)
    use_doc_unwarping = Column(Boolean, default=False)
    use_chart_recognition = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UploadBatch(Base):
    """上传批次表"""
    __tablename__ = "upload_batches"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    original_filename = Column(String(255), nullable=False)
    subject = Column(String(50))
    file_path = Column(Text)
    upload_time = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="upload_batches")
    project = relationship("Project", back_populates="upload_batches")
    questions = relationship("Question", back_populates="batch")


class Question(Base):
    """题目表"""
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    batch_id = Column(Integer, ForeignKey("upload_batches.id"))
    content_hash = Column(String(64), nullable=False)
    question_type = Column(String(20))
    content_json = Column(Text)
    options_json = Column(Text)
    has_formula = Column(Boolean, default=False)
    has_image = Column(Boolean, default=False)
    image_refs_json = Column(Text)
    # 题目所在整页原图引用（/api/question-image/...），供前端展示整页
    page_image_ref = Column(Text, nullable=True)
    needs_correction = Column(Boolean, default=False)
    ocr_issues_json = Column(Text)
    user_answer = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    review_status = Column(String(10), nullable=True, default='待复习', index=True)
    review_due_at = Column(DateTime, nullable=True, index=True)
    review_last_at = Column(DateTime, nullable=True)
    review_interval_days = Column(Integer, default=0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    ease_factor = Column(Float, default=2.5, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    answer = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('content_hash', 'user_id', 'project_id', name='uq_question_hash_user_project'),
    )

    user = relationship("User", back_populates="questions")
    project = relationship("Project", back_populates="questions")
    batch = relationship("UploadBatch", back_populates="questions")
    tags = relationship("QuestionTagMapping", back_populates="question")
    chat_sessions = relationship("ChatSession", back_populates="question")
    embedding = relationship("QuestionEmbedding", back_populates="question", uselist=False, cascade="all, delete-orphan")


class QuestionEmbedding(Base):
    """Cached vector representation for natural-language question search."""
    __tablename__ = "question_embeddings"

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False, unique=True, index=True)
    model_name = Column(String(50), nullable=False, default="local-hash-v1")
    text_hash = Column(String(64), nullable=False)
    vector_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    question = relationship("Question", back_populates="embedding")


class ChatSession(Base):
    """对话会话（可绑定题目，也可独立对话）"""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    public_id = Column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True, index=True)
    title = Column(String(255), default="新对话")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    question = relationship("Question", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.id")


class ChatMessage(Base):
    """对话消息"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")


class SplitRecord(Base):
    """分割历史记录表"""
    __tablename__ = "split_records"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    subject = Column(String(50))
    model_provider = Column(String(20))
    file_names_json = Column(Text)
    original_images_json = Column(Text)
    questions_json = Column(Text)
    question_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="split_records")


class WorkflowRun(Base):
    """一次可追踪的后端工作流运行记录。

    当前由 SQLite 持久化，业务层通过 core.workflow_run_store 访问；
    后续替换为 Redis/任务队列时，尽量只替换 store 层。
    """
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True)
    public_id = Column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    run_type = Column(String(30), nullable=False, default="split", index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    subject = Column(String(50), nullable=True)
    model_provider = Column(String(20), nullable=True)
    file_names_json = Column(Text, nullable=True)
    result_dir = Column(Text, nullable=False, default="")
    question_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="workflow_runs")


class KnowledgeTag(Base):
    """知识点标签表"""
    __tablename__ = "knowledge_tags"

    id = Column(Integer, primary_key=True)
    tag_name = Column(String(50), nullable=False)
    subject = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tag_name", "subject", name="uq_tag_subject"),
    )


class QuestionTagMapping(Base):
    """题目-标签关联表"""
    __tablename__ = "question_tag_mapping"

    question_id = Column(Integer, ForeignKey("questions.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("knowledge_tags.id"), primary_key=True)

    question = relationship("Question", back_populates="tags")
    tag = relationship("KnowledgeTag")


class Note(Base):
    """笔记表"""
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)          # 笔记标题
    subject = Column(String(50))                          # 科目
    content_markdown = Column(Text, default="")           # LLM 整理后的 Markdown 内容
    source_images_json = Column(Text)                     # 原始上传图片路径列表 JSON
    ocr_text = Column(Text)                               # OCR 识别的原始文本（保留用于重新整理）
    review_due_at = Column(DateTime, nullable=True, index=True)
    review_last_at = Column(DateTime, nullable=True)
    review_interval_days = Column(Integer, default=0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    ease_factor = Column(Float, default=2.5, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="notes")
    project = relationship("Project", back_populates="notes")
    tags = relationship("NoteTagMapping", back_populates="note", cascade="all, delete-orphan")


class NoteTagMapping(Base):
    """笔记-标签关联表（与错题共享 KnowledgeTag）"""
    __tablename__ = "note_tag_mapping"

    note_id = Column(Integer, ForeignKey("notes.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("knowledge_tags.id"), primary_key=True)

    note = relationship("Note", back_populates="tags")
    tag = relationship("KnowledgeTag")


class ReviewEvent(Base):
    """Spaced-repetition review history for questions and notes."""
    __tablename__ = "review_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    target_type = Column(String(20), nullable=False, index=True)
    target_id = Column(Integer, nullable=False, index=True)
    rating = Column(String(20), nullable=False)
    quality = Column(Integer, default=3, nullable=False)
    interval_days = Column(Integer, default=0, nullable=False)
    ease_factor = Column(Float, default=2.5, nullable=False)
    reviewed_at = Column(DateTime, default=datetime.utcnow, index=True)
    next_due_at = Column(DateTime, nullable=True, index=True)


class EmailVerification(Base):
    """注册邮箱验证码（仅存哈希，不存明文）"""
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
