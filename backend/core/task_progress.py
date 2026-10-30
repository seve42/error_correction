"""导入试卷流程的进程内进度事件中心。

导入试卷是分钟级长任务（视觉识别、分割、纠错、导出），前端需要看到每一步的
实时日志、每个 AI 调用的原始输出以及真实进度条。事件只对当前浏览器会话有意
义：页面关闭即中断，落库只会产生垃圾数据，因此这里用内存字典 + 条件变量保存。
多进程部署时 SSE 订阅与写事件必须落在同一进程，当前 Flask 单进程多线程部署满足
这一前提。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any, Optional

# 单任务事件上限：几百张图片会产生上千条批次日志，超出后丢弃最旧事件，
# 但 seq 继续递增，前端按 after 续连时不会重复收到旧事件。
MAX_EVENTS = 1000

# 同时在跑的任务上限，防止长期运行后内存无限增长。
MAX_TASKS = 50

# 任务空闲多久后清理（秒）。按最后一次活动时间计算，SSE 保活与轮询都会刷新，
# 保证正在被观察的任务不会被误删。
TASK_TTL_SECONDS = 3600

# SSE 无新事件时的保活间隔（秒），与前端契约一致；也用于避免代理断开空闲连接。
STREAM_KEEPALIVE_SECONDS = 15

# 先连 SSE 再发 POST 时，占位任务最多等待 POST 到达的时间（秒）。
PLACEHOLDER_WAIT_SECONDS = 20

# 单次 AI 调用原始输出在事件里的截断上限，避免一条事件把 SSE 撑爆。
AI_OUTPUT_LIMIT = 20000

VALID_LEVELS = ("info", "success", "warn", "error")

_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}")


class TaskAccessError(Exception):
    """任务属于其他用户。

    路由层统一转成 403/404，避免通过错误码差异泄露某个 task_id 是否存在。
    """


class _TaskState:
    """单个任务的事件容器；所有字段只在事件中心的锁内读写。"""

    __slots__ = (
        "task_id",
        "user_id",
        "kind",
        "created_at",
        "updated_at",
        "events",
        "finished",
        "ok",
        "message",
        "next_seq",
    )

    def __init__(self, task_id: str, user_id: Any, kind: str, now: float):
        self.task_id = task_id
        self.user_id = user_id
        self.kind = kind
        self.created_at = now
        self.updated_at = now
        self.events: deque[dict] = deque()
        self.finished = False
        # ok 为 None 表示任务尚未结束；只有 finish() 才会给出明确的成功/失败
        self.ok = None
        self.message = ""
        self.next_seq = 0


_lock = threading.Lock()
_condition = threading.Condition(_lock)
_tasks: dict[str, _TaskState] = {}


def is_valid_task_id(task_id: Any) -> bool:
    """task_id 由前端 crypto.randomUUID() 生成，只允许 URL 安全字符。"""
    if not isinstance(task_id, str):
        return False
    return _TASK_ID_PATTERN.fullmatch(task_id) is not None


def _purge_expired_locked(now: float) -> None:
    expired = [tid for tid, task in _tasks.items() if now - task.updated_at > TASK_TTL_SECONDS]
    for tid in expired:
        _tasks.pop(tid, None)


def _evict_oldest_locked(keep_task_id: str) -> None:
    """任务数达到上限时淘汰最久未活动的一个，保留刚创建/刚被访问的任务。"""
    victims = [tid for tid in _tasks if tid != keep_task_id]
    if not victims:
        return
    oldest = min(victims, key=lambda tid: _tasks[tid].updated_at)
    _tasks.pop(oldest, None)


def _task_meta_locked(task: _TaskState) -> dict:
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "finished": task.finished,
        "ok": task.ok,
        "message": task.message,
    }


def ensure_task(task_id: str, user_id: Any, kind: str = "", *, restart: bool = False) -> dict:
    """登记任务并校验归属，返回任务元信息。

    restart=True 用于同一 task_id 连续跑 OCR、分割两个阶段（reopen）：保留已有
    事件与 seq，仅把 finished 置回 False、ok 置回 None、message 清空。前端在
    上一阶段 done 后按 after=上一个 seq 重连，既不会重复收到旧事件，也不会在
    新阶段 POST 到达前就收到 done。
    非 POST 场景（SSE 占位、轮询）必须保持 restart=False，避免把已结束任务
    重新打开。
    """
    if not is_valid_task_id(task_id):
        raise ValueError(f"非法 task_id: {task_id!r}")
    now = time.time()
    with _condition:
        _purge_expired_locked(now)
        task = _tasks.get(task_id)
        if task is not None:
            if task.user_id != user_id:
                raise TaskAccessError(task_id)
            task.kind = kind or task.kind
            task.updated_at = now
            if restart:
                task.finished = False
                task.ok = None
                task.message = ""
            _condition.notify_all()
            return _task_meta_locked(task)
        if len(_tasks) >= MAX_TASKS:
            _evict_oldest_locked(task_id)
        task = _TaskState(task_id, user_id, kind, now)
        _tasks[task_id] = task
        return _task_meta_locked(task)


def emit(
    task_id: Optional[str],
    *,
    step: str,
    message: str,
    level: str = "info",
    stage: str = "",
    progress: Optional[float] = None,
    detail: Optional[dict] = None,
) -> Optional[dict]:
    """追加一条进度事件；任务不存在时静默忽略。

    进度日志属于「尽力而为」的旁路：任务被清理或 task_id 未登记时不能影响
    主流程（OCR/分割）继续执行，所以这里不抛异常只返回 None。
    """
    if not task_id:
        return None
    if level not in VALID_LEVELS:
        level = "info"
    if progress is not None:
        try:
            progress = round(min(1.0, max(0.0, float(progress))), 4)
        except (TypeError, ValueError):
            progress = None
    now = time.time()
    with _condition:
        task = _tasks.get(task_id)
        if task is None:
            return None
        task.next_seq += 1
        event = {
            "seq": task.next_seq,
            "ts": round(now, 3),
            "level": level,
            "step": step,
            "stage": stage,
            "message": message,
            "progress": progress,
            "detail": detail,
        }
        task.events.append(event)
        if len(task.events) > MAX_EVENTS:
            task.events.popleft()
        task.updated_at = now
        _condition.notify_all()
        return event


def finish(task_id: Optional[str], *, ok: bool, message: str = "") -> None:
    """标记任务结束并唤醒订阅者；调用方保证之后不会再 emit 该任务。"""
    if not task_id:
        return
    now = time.time()
    with _condition:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.finished = True
        task.ok = bool(ok)
        task.message = message or ""
        task.updated_at = now
        _condition.notify_all()


def _normalize_after(after: Any) -> int:
    try:
        return max(0, int(after or 0))
    except (TypeError, ValueError):
        return 0


def snapshot(task_id: str, after: int = 0, user_id: Any = None) -> Optional[dict]:
    """返回 seq > after 的事件与结束状态；任务不存在返回 None。"""
    after = _normalize_after(after)
    now = time.time()
    with _condition:
        task = _tasks.get(task_id)
        if task is None:
            return None
        if user_id is not None and task.user_id != user_id:
            raise TaskAccessError(task_id)
        task.updated_at = now
        return {
            "events": [event for event in task.events if event["seq"] > after],
            "finished": task.finished,
            "ok": task.ok,
            "message": task.message,
        }


def wait_events(task_id: str, after: int = 0, timeout: float = STREAM_KEEPALIVE_SECONDS, user_id: Any = None) -> dict:
    """阻塞等待新事件，最多 timeout 秒。

    返回结构与 snapshot 一致。超时返回空事件列表，调用方据此发 SSE 保活帧。
    等待期间也会刷新任务的 updated_at，避免正在被订阅的任务被 TTL 清理掉。
    """
    after = _normalize_after(after)
    deadline = time.time() + max(0.0, float(timeout or 0))
    with _condition:
        while True:
            task = _tasks.get(task_id)
            if task is None:
                return {"events": [], "finished": True, "ok": False, "message": "任务不存在或已过期"}
            if user_id is not None and task.user_id != user_id:
                raise TaskAccessError(task_id)
            pending = [event for event in task.events if event["seq"] > after]
            if pending or task.finished:
                task.updated_at = time.time()
                return {
                    "events": pending,
                    "finished": task.finished,
                    "ok": task.ok,
                    "message": task.message,
                }
            remaining = deadline - time.time()
            if remaining <= 0:
                task.updated_at = time.time()
                return {"events": [], "finished": False, "ok": False, "message": ""}
            _condition.wait(remaining)


def build_ai_call_detail(
    *,
    model: str,
    pages: Optional[list],
    duration: float,
    tokens: Optional[dict] = None,
    attempt: int = 1,
    output: str = "",
) -> dict:
    """构造 AI 调用事件的 detail。

    output 保留原始返回 JSON 的前 AI_OUTPUT_LIMIT 个字符，前端展开即可看到模型
    真正输出了什么；截断标记让前端提示“内容过长已截断”。pages 用 1 基页码，
    与日志文案「第 a-b 页」保持一致，方便前端直接展示。
    """
    if isinstance(output, str):
        text = output
    else:
        try:
            text = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(output)
    detail: dict[str, Any] = {
        "kind": "ai_call",
        "model": str(model or ""),
        "pages": [int(page) for page in (pages or [])],
        "duration": round(float(duration or 0), 1),
        "attempt": int(attempt or 1),
        "output": text[:AI_OUTPUT_LIMIT],
        "output_truncated": len(text) > AI_OUTPUT_LIMIT,
    }
    if tokens:
        prompt = tokens.get("prompt", tokens.get("prompt_tokens"))
        completion = tokens.get("completion", tokens.get("completion_tokens"))
        total = tokens.get("total", tokens.get("total_tokens"))
        detail["tokens"] = {
            "prompt": int(prompt or 0),
            "completion": int(completion or 0),
            "total": int(total or 0),
        }
    return detail


class EventEmitter:
    """进度事件出口。

    task_id 为空时所有方法退化为 no-op：note 模式、单元测试和其它调用方不需要
    关系进度事件，传入 None 即可完全屏蔽副作用。
    """

    __slots__ = ("task_id",)

    def __init__(self, task_id: Optional[str] = None):
        self.task_id = task_id or None

    @property
    def enabled(self) -> bool:
        return bool(self.task_id)

    def emit(
        self,
        *,
        step: str,
        message: str,
        level: str = "info",
        stage: str = "",
        progress: Optional[float] = None,
        detail: Optional[dict] = None,
    ) -> None:
        if not self.task_id:
            return
        emit(
            self.task_id,
            step=step,
            message=message,
            level=level,
            stage=stage,
            progress=progress,
            detail=detail,
        )

    def finish(self, *, ok: bool, message: str = "") -> None:
        if not self.task_id:
            return
        finish(self.task_id, ok=ok, message=message)
