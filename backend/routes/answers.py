"""标准答案解析生成路由

供题目录入流程调用：把前端整理好的题目交给解题智能体，返回标准答案与完整解析。
接口字段名已与前端约定，改动需同步前端。

答案解析已是导入流程中的独立一步：前端沿用 OCR/分割的同一个 task_id 续接日志，
所以这里复用 core.task_progress 事件中心，把分批进度、重试和 AI 原始输出实时推给前端。
"""

import logging
from typing import Optional

from flask import Blueprint, request, jsonify, session

from db import SessionLocal
from db import crud
from core import task_progress
from core.model_selection import LLMSelectionError, resolve_llm_selection
from core.quota import (
    has_daily_free_quota,
    quota_exceeded_response,
    uses_server_llm_selection,
)
from core.task_progress import EventEmitter, TaskAccessError
from core.usage import end_usage, start_usage
from agents.solve import invoke_solve
from agents.solve.agent import ANSWER_STAGE, ANSWER_STEP

logger = logging.getLogger(__name__)

bp = Blueprint("answers", __name__)


def _parse_task_id(payload: dict) -> Optional[str]:
    """从请求体读取可选的 task_id（前端 crypto.randomUUID 生成）。"""
    raw = payload.get("task_id")
    if raw is None or raw == "":
        return None
    value = str(raw).strip()
    if not task_progress.is_valid_task_id(value):
        raise ValueError("task_id 非法")
    return value


def _start_progress(task_id: Optional[str], user_id, kind: str) -> EventEmitter:
    """登记进度任务并返回事件出口。

    同一 task_id 前面已经跑过 OCR 与分割：restart=True 只清完成标记，历史事件与
    seq 保留，前端按 after=上一个 seq 续连即可看到解答阶段的日志。
    """
    if not task_id:
        return EventEmitter(None)
    task_progress.ensure_task(task_id, user_id, kind=kind, restart=True)
    return EventEmitter(task_id)


def _finish_ok(emitter: EventEmitter, message: str) -> None:
    """成功收尾：先发 success 事件再结束任务，轮询兜底也能读到最终文案。"""
    emitter.emit(step="done", stage="完成", level="success", message=message, progress=1.0)
    emitter.finish(ok=True, message=message)


def _finish_failed(emitter: EventEmitter, message: str) -> None:
    """失败收尾：错误事件 + 结束标记，前端无需等到超时。"""
    emitter.emit(step="done", stage="完成", level="error", message=message)
    emitter.finish(ok=False, message=message)


def _build_content_text(content_blocks) -> tuple[str, list[str]]:
    """把 content_blocks 拼成解题输入：文字按顺序拼接，图片保留引用路径作为说明。"""
    parts: list[str] = []
    image_refs: list[str] = []
    for block in content_blocks or []:
        if not isinstance(block, dict):
            continue
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        if block.get("block_type") == "image":
            image_refs.append(content)
            parts.append(f"[图片: {content}]")
        else:
            parts.append(content)
    return "\n".join(parts), image_refs


def _build_solve_input(question: dict, index: int) -> dict:
    """组装单题解题输入，不传超长字段，只保留题干文本、选项与图片说明。"""
    content, image_refs = _build_content_text(question.get("content_blocks"))
    question_id = question.get("question_id")
    if question_id in (None, ""):
        question_id = question.get("uid") or f"q_{index}"

    item = {
        "question_id": str(question_id),
        "question_type": question.get("question_type") or "",
        "content": content,
        "options": question.get("options") or [],
        "has_image": bool(image_refs),
    }
    if image_refs:
        item["image_refs"] = image_refs
    return item


@bp.route("/generate-answers", methods=["POST"])
def generate_answers():
    """按所选模型为题目批量生成标准答案与解析，结果顺序与请求题目一致。"""
    data = request.get_json(silent=True) or {}
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return jsonify({"success": False, "error": "题目不能为空"}), 400
    if not all(isinstance(question, dict) for question in questions):
        return jsonify({"success": False, "error": "题目格式不正确"}), 400

    user_id = session.get("user_id")
    try:
        task_id = _parse_task_id(data)
    except ValueError:
        return jsonify({"success": False, "error": "task_id 非法", "code": "INVALID_TASK_ID"}), 400
    try:
        emitter = _start_progress(task_id, user_id, "answers")
    except TaskAccessError:
        return jsonify({"success": False, "error": "任务不存在"}), 403

    model_provider = data.get("model_provider") or "openai"
    model_name = data.get("model_name") or None
    provider_source = data.get("provider_source") or None
    provider_id = data.get("provider_id") or None

    with SessionLocal() as db:
        try:
            selection = resolve_llm_selection(
                db,
                user_id=user_id,
                category=model_provider,
                model_name=model_name,
                provider_source=provider_source,
                provider_id=provider_id,
            )
        except LLMSelectionError as e:
            _finish_failed(emitter, e.message)
            return (
                jsonify({"success": False, "code": e.code, "error": e.message}),
                e.status_code,
            )

        # 与对话接口一致：只有平台托管模型才占用每日免费点数。
        should_consume_quota = bool(user_id) and uses_server_llm_selection(
            selection["source"],
            db=db,
            user_id=user_id,
            provider=model_provider,
        )
        if should_consume_quota:
            quota_user = crud.get_user_by_id(db, user_id)
            if not quota_user:
                session.clear()
                _finish_failed(emitter, "用户不存在")
                return jsonify({"success": False, "error": "用户不存在"}), 401
            if not has_daily_free_quota(db, quota_user):
                payload, status = quota_exceeded_response(db, quota_user)
                message = payload.get("error") or "今日免费体验次数已用完"
                _finish_failed(emitter, message)
                return jsonify(payload), status

    solve_inputs = [
        _build_solve_input(question, index) for index, question in enumerate(questions)
    ]
    total = len(solve_inputs)
    emitter.emit(
        step=ANSWER_STEP,
        stage=ANSWER_STAGE,
        level="info",
        message=f"开始生成标准答案解析：共 {total} 道题目",
        progress=0.0,
    )

    usage_ctx = start_usage(
        user_id,
        "chat",
        provider_source=selection["source"],
        system_stages={"llm"} if should_consume_quota else set(),
    )
    try:
        solve_result = invoke_solve(
            solve_inputs,
            provider=selection["category"],
            model_name=selection["model_name"],
            emitter=emitter,
        )
    except Exception:
        logger.exception("生成标准答案解析失败")
        _finish_failed(emitter, "生成标准答案解析失败，请稍后重试")
        return jsonify({"success": False, "error": "AI 生成答案解析失败，请稍后重试"}), 502
    finally:
        end_usage(usage_ctx)

    # invoke_solve 保证结果与输入顺序一致；仍做越界防御，避免个别题漏答导致错位。
    results = []
    for index, question in enumerate(questions):
        solved = (
            solve_result.results[index] if index < len(solve_result.results) else None
        )
        results.append(
            {
                "uid": question.get("uid"),
                "id": question.get("id"),
                "answer": (solved.answer if solved else "").strip(),
                "reasoning": (solved.reasoning if solved else "").strip(),
                "confidence": solved.confidence if solved else 0.0,
            }
        )

    answered = sum(1 for item in results if item["answer"])
    summary = f"标准答案解析生成完成：{answered}/{total} 道题"
    _finish_ok(emitter, summary)
    return jsonify({"success": True, "results": results})
