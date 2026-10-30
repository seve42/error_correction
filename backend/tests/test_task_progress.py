"""进度事件中心（core/task_progress.py）的关键行为。

不依赖数据库与真实模型调用：事件中心本身只做内存缓冲与等待唤醒。
"""

import threading
import time
import uuid

import pytest

from core.task_progress import (
    EventEmitter,
    TaskAccessError,
    build_ai_call_detail,
    emit,
    ensure_task,
    finish,
    is_valid_task_id,
    snapshot,
    wait_events,
)

USER_ID = 1001


def _new_task_id():
    return uuid.uuid4().hex


def test_emit_keeps_order_and_supports_after_filter():
    task_id = _new_task_id()
    ensure_task(task_id, USER_ID, kind="ocr")

    first = emit(task_id, step="prepare", stage="准备", message="正在准备")
    emit(task_id, step="vision_batch", stage="视觉识别", message="第二批", level="success", progress=0.5)

    state = snapshot(task_id)
    assert [event["seq"] for event in state["events"]] == [1, 2]
    assert [event["message"] for event in state["events"]] == ["正在准备", "第二批"]
    assert snapshot(task_id, after=1)["events"] == [state["events"][1]]
    # 事件字段与契约一致，detail 缺省为 None
    assert set(first) == {"seq", "ts", "level", "step", "stage", "message", "progress", "detail"}
    assert first["detail"] is None


def test_wait_events_wakes_up_on_emit():
    task_id = _new_task_id()
    ensure_task(task_id, USER_ID, kind="ocr")
    result = {}

    def _wait():
        result.update(wait_events(task_id, after=0, timeout=5, user_id=USER_ID))

    waiter = threading.Thread(target=_wait)
    waiter.start()
    time.sleep(0.05)
    emit(task_id, step="split", stage="分割题目", message="分割中")
    waiter.join(timeout=5)

    assert not waiter.is_alive()
    assert [event["message"] for event in result["events"]] == ["分割中"]
    assert result["finished"] is False


def test_finish_marks_task_finished_and_ok():
    task_id = _new_task_id()
    ensure_task(task_id, USER_ID, kind="ocr")
    emit(task_id, step="done", stage="完成", level="success", message="视觉识别完成", progress=1.0)
    finish(task_id, ok=True, message="视觉识别完成，共 3 页")

    state = snapshot(task_id)
    assert state["finished"] is True
    assert state["ok"] is True
    assert state["message"] == "视觉识别完成，共 3 页"
    # 完成后 wait_events 与 snapshot 同形，便于轮询/SSE 复用
    done = wait_events(task_id, after=0, timeout=1, user_id=USER_ID)
    assert done["finished"] is True and done["ok"] is True


def test_invalid_task_id_rejected():
    for bad in ("", "short", "x" * 65, "bad space", "任务标识", None, 123):
        assert is_valid_task_id(bad) is False
        with pytest.raises(ValueError):
            ensure_task(bad, USER_ID)


def test_cross_user_access_rejected():
    task_id = _new_task_id()
    ensure_task(task_id, USER_ID)
    other_user = USER_ID + 1

    with pytest.raises(TaskAccessError):
        ensure_task(task_id, other_user)
    with pytest.raises(TaskAccessError):
        snapshot(task_id, user_id=other_user)
    with pytest.raises(TaskAccessError):
        wait_events(task_id, after=0, timeout=0.1, user_id=other_user)


def test_restart_keeps_history_for_next_phase():
    """OCR 与分割复用同一 task_id：restart 只清完成标记，seq 继续递增。"""
    task_id = _new_task_id()
    ensure_task(task_id, USER_ID, kind="ocr")
    emit(task_id, step="done", stage="完成", message="OCR 完成")
    finish(task_id, ok=True, message="视觉识别完成，共 2 页")

    ensure_task(task_id, USER_ID, kind="split", restart=True)
    state = snapshot(task_id)
    assert state["finished"] is False
    assert [event["seq"] for event in state["events"]] == [1]

    emit(task_id, step="split", stage="分割题目", message="开始分割")
    assert [event["seq"] for event in snapshot(task_id)["events"]] == [1, 2]


def test_noop_emitter_ignores_calls():
    emitter = EventEmitter(None)
    assert emitter.enabled is False
    assert emitter.emit(step="prepare", stage="准备", message="忽略") is None
    assert emitter.finish(ok=True, message="忽略") is None


def test_ai_call_detail_truncates_output():
    detail = build_ai_call_detail(
        model="deepseek-flash",
        pages=[7, 8],
        duration=12.34,
        tokens={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        attempt=2,
        output="x" * 25000,
    )
    assert detail["kind"] == "ai_call"
    assert detail["model"] == "deepseek-flash"
    assert detail["pages"] == [7, 8]  # 调用方传入 1 基页码，原样透出
    assert detail["tokens"] == {"prompt": 10, "completion": 5, "total": 15}
    assert detail["duration"] == 12.3
    assert detail["attempt"] == 2
    assert len(detail["output"]) == 20000
    assert detail["output_truncated"] is True
