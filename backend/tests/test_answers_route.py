"""
POST /api/generate-answers 路由测试

覆盖接口契约：成功响应的字段/顺序、解题输入组装、平台额度不足 429。
"""

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.models import Base, SystemProviderConfig, User
from agents.solve.schemas import SolveBatchResult, SolveResult

TEST_USER_ID = 1


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_user_and_provider(db):
    user = User(id=TEST_USER_ID, username="test", email="test@test.com", password_hash="x")
    user.daily_free_quota = 100
    user.daily_free_used = 0
    user.daily_free_quota_date = datetime.utcnow().date().isoformat()
    db.add(user)
    db.add(
        SystemProviderConfig(
            id="system-openai-test",
            category="openai",
            name="测试平台 OpenAI",
            is_active=True,
            api_key="sk-system-test",
            model_name="gpt-4o-mini",
        )
    )
    db.commit()


@pytest.fixture
def client(test_db, monkeypatch):
    _seed_user_and_provider(test_db)
    # 固定默认额度，避免部署环境 APP_DAILY_FREE_QUOTA 影响断言。
    monkeypatch.setattr("core.config.settings.daily_free_quota", 100)

    class FakeSessionLocal:
        def __call__(self):
            return self

        def __enter__(self):
            return test_db

        def __exit__(self, *args):
            pass

    fake = FakeSessionLocal()
    with patch("web_app.SessionLocal", fake), patch(
        "routes.answers.SessionLocal", fake
    ), patch("db.SessionLocal", fake):
        from web_app import app

        app.config["TESTING"] = True
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = TEST_USER_ID
                sess["username"] = "test"
                sess["session_version"] = 0
            yield c


def test_generate_answers_contract(client, monkeypatch):
    captured = {}

    def fake_invoke_solve(questions, provider="openai", max_retries=3, **kwargs):
        captured.update(questions=questions, provider=provider, model_name=kwargs.get("model_name"))
        return SolveBatchResult(
            results=[
                SolveResult(
                    question_id=question["question_id"],
                    answer="A",
                    reasoning="由 $1+1=2$ 可知正确答案为 A。",
                    confidence=0.9,
                )
                for question in questions
            ]
        )

    monkeypatch.setattr("routes.answers.invoke_solve", fake_invoke_solve)

    payload = {
        "questions": [
            {
                "uid": "q_0",
                "id": 7,
                "question_id": "1",
                "question_type": "选择题",
                "content_blocks": [
                    {"block_type": "text", "content": "$1+1=$"},
                    {"block_type": "text", "content": "（ ）"},
                ],
                "options": ["A. 2", "B. 3"],
            },
            {
                "uid": None,
                "id": None,
                "question_id": "2",
                "question_type": "填空题",
                "content_blocks": [
                    {"block_type": "text", "content": "如图，求 $x$"},
                    {"block_type": "image", "content": "/images/a.png"},
                ],
                "options": [],
            },
        ],
        "model_provider": "openai",
        "model_name": "gpt-4o-mini",
    }
    resp = client.post("/api/generate-answers", json=payload)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert [r["uid"] for r in data["results"]] == ["q_0", None]
    assert [r["id"] for r in data["results"]] == [7, None]
    assert data["results"][0]["answer"] == "A"
    assert data["results"][0]["reasoning"].startswith("由")
    assert data["results"][0]["confidence"] == 0.9

    # 解题输入：text 按顺序拼接，含图题目标注图片引用并置 has_image
    first, second = captured["questions"]
    assert first["question_id"] == "1"
    assert first["content"] == "$1+1=$\n（ ）"
    assert first["options"] == ["A. 2", "B. 3"]
    assert first["has_image"] is False
    assert second["has_image"] is True
    assert "[图片: /images/a.png]" in second["content"]
    assert captured["provider"] == "openai"
    assert captured["model_name"] == "gpt-4o-mini"


def test_generate_answers_quota_exceeded(client, test_db):
    user = test_db.query(User).filter(User.id == TEST_USER_ID).first()
    user.daily_free_used = user.daily_free_quota
    test_db.commit()

    resp = client.post(
        "/api/generate-answers",
        json={
            "questions": [
                {
                    "uid": "q_0",
                    "question_id": "1",
                    "question_type": "选择题",
                    "content_blocks": [{"block_type": "text", "content": "1+1=?"}],
                    "options": ["A. 2", "B. 3"],
                }
            ],
            "model_provider": "openai",
        },
    )

    assert resp.status_code == 429
    data = resp.get_json()
    assert data["success"] is False
    assert data["code"] == "DAILY_FREE_QUOTA_EXCEEDED"
