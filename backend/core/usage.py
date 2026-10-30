"""模型调用用量记录：token、费用（微元）与免费点数扣减。

设计：
- 所有 LangChain 调用通过 create_llm 注入的 UsageCallbackHandler 上报；
- 视觉 OCR 的原生 OpenAI 调用通过 record_native_usage 上报；
- 请求入口用 start_usage / usage_context 设置上下文，请求结束时 flush，
  把逐次调用写入 model_usage_events，并按实际费用折算点数扣减免费额度。
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

_BEIJING = ZoneInfo("Asia/Shanghai")
_MICRO_PER_YUAN = 1_000_000
# 1 元 = 10 点 → 1 点 = 0.1 元 = 100000 微元
MICRO_PER_POINT = 100_000

# 价格单位：元 / 百万 token。默认价按 DeepSeek 空闲时段，峰时再乘 peak_multiplier。
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek-flash": {"miss": 1.0, "hit": 0.1, "out": 4.0, "peak_multiplier": 2.0},
    "deepseek-v4-flash": {"miss": 1.0, "hit": 0.1, "out": 4.0, "peak_multiplier": 2.0},
    "deepseek-v4-pro": {"miss": 2.0, "hit": 0.2, "out": 8.0, "peak_multiplier": 1.0},
    "deepseek": {"miss": 1.0, "hit": 0.1, "out": 4.0, "peak_multiplier": 2.0},
    "default": {"miss": 0.0, "hit": 0.0, "out": 0.0, "peak_multiplier": 1.0},
}

# 峰时窗口（北京时间，工作日）：价格按 peak_multiplier 上浮。
_PEAK_WINDOWS = ((9, 12), (14, 18))


def _price_table() -> dict[str, dict[str, float]]:
    table = {key: dict(value) for key, value in _DEFAULT_PRICES.items()}
    try:
        from core.config import settings

        raw = (getattr(settings, "model_prices", "") or "").strip()
        if raw:
            custom = json.loads(raw)
            if isinstance(custom, dict):
                for key, value in custom.items():
                    if isinstance(value, dict):
                        table[str(key)] = {**table.get(str(key), {}), **value}
    except Exception:  # noqa: BLE001
        logger.warning("解析 APP_MODEL_PRICES 失败，使用默认价目表", exc_info=True)
    return table


def _resolve_prices(model_name: str) -> tuple[dict[str, float], bool]:
    table = _price_table()
    key = (model_name or "").strip().lower()
    matched = None
    for candidate in sorted(table, key=len, reverse=True):
        if candidate == "default":
            continue
        probe = candidate.rstrip("*")
        if key.startswith(probe):
            matched = candidate
            break
    if matched is None:
        return table["default"], True
    return table[matched], False


def _is_peak(now: datetime | None = None) -> bool:
    local = (now or datetime.now(_BEIJING)).astimezone(_BEIJING)
    if local.weekday() >= 5:
        return False
    return any(start <= local.hour < end for start, end in _PEAK_WINDOWS)


def estimate_cost_micro(
    model_name: str,
    *,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    cache_creation_tokens: int = 0,
    now: datetime | None = None,
) -> int:
    """估算一次调用的费用（微元）。单价按 元/百万 token，token×单价 即微元。"""
    prices, _ = _resolve_prices(model_name)
    multiplier = float(prices.get("peak_multiplier") or 1.0) if _is_peak(now) else 1.0
    prompt = max(int(prompt_tokens or 0), 0)
    cached = max(int(cached_tokens or 0), 0)
    write = max(int(cache_creation_tokens or 0), 0)
    completion = max(int(completion_tokens or 0), 0)
    miss = max(prompt - cached - write, 0)
    cost = (
        miss * float(prices.get("miss") or 0.0)
        + cached * float(prices.get("hit") or 0.0)
        + write * float(prices.get("miss") or 0.0)
        + completion * float(prices.get("out") or 0.0)
    ) * multiplier
    return int(round(cost))


def points_for_cost(cost_micro_yuan: int) -> int:
    """按实际费用折算点数：1 元 = 10 点，取整到 1 点，不足 1 点按 1 点。"""
    cost = max(int(cost_micro_yuan or 0), 0)
    if cost <= 0:
        return 0
    return max(1, math.ceil(cost / MICRO_PER_POINT))


def _today_str() -> str:
    return datetime.utcnow().date().isoformat()


@dataclass
class UsageEntry:
    provider: str
    model_name: str
    stage: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_micro_yuan: int = 0
    billable: bool = False


@dataclass
class UsageContext:
    user_id: int | None = None
    action_type: str = "other"
    run_id: str | None = None
    provider_source: str = ""
    system_stages: set[str] = field(default_factory=set)
    entries: list[UsageEntry] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    flushed: bool = False

    def append(self, entry: UsageEntry) -> None:
        with self.lock:
            self.entries.append(entry)


_usage_ctx: contextvars.ContextVar[UsageContext | None] = contextvars.ContextVar(
    "model_usage_context", default=None
)


def current_usage_context() -> UsageContext | None:
    return _usage_ctx.get()


def start_usage(
    user_id: int | None = None,
    action_type: str = "other",
    *,
    run_id: str | None = None,
    provider_source: str = "",
    system_stages: set[str] | tuple[str, ...] | None = None,
) -> UsageContext:
    ctx = UsageContext(
        user_id=user_id,
        action_type=action_type or "other",
        run_id=run_id,
        provider_source=provider_source or "",
        system_stages=set(system_stages or ()),
    )
    _usage_ctx.set(ctx)
    return ctx


@contextmanager
def usage_context(
    user_id: int | None = None,
    action_type: str = "other",
    *,
    run_id: str | None = None,
    provider_source: str = "",
    system_stages: set[str] | tuple[str, ...] | None = None,
):
    token = _usage_ctx.set(
        UsageContext(
            user_id=user_id,
            action_type=action_type or "other",
            run_id=run_id,
            provider_source=provider_source or "",
            system_stages=set(system_stages or ()),
        )
    )
    try:
        yield _usage_ctx.get()
    finally:
        ctx = _usage_ctx.get()
        _usage_ctx.reset(token)
        flush_usage(ctx)


def end_usage(ctx: UsageContext | None) -> None:
    if ctx is None:
        return
    _usage_ctx.set(None)
    flush_usage(ctx)


def _extract_usage_from_llm_result(response: Any) -> dict[str, Any] | None:
    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            usage_metadata = getattr(message, "usage_metadata", None)
            if usage_metadata:
                return {"usage_metadata": usage_metadata}
            response_metadata = getattr(message, "response_metadata", None) or {}
            raw = response_metadata.get("token_usage") or response_metadata.get("usage")
            if raw:
                return {"raw": raw}
    llm_output = getattr(response, "llm_output", None) or {}
    raw = llm_output.get("token_usage") or llm_output.get("usage")
    if raw:
        return {"raw": raw}
    return None


def _normalize_usage(payload: dict[str, Any]) -> dict[str, int]:
    if "usage_metadata" in payload:
        meta = payload["usage_metadata"] or {}
        prompt = int(meta.get("input_tokens") or 0)
        completion = int(meta.get("output_tokens") or 0)
        total = int(meta.get("total_tokens") or (prompt + completion))
        details = meta.get("input_token_details") or {}
        cached = int(details.get("cache_read") or 0)
        cache_write = int(details.get("cache_creation") or 0)
        return {
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cache_creation_tokens": cache_write,
            "completion_tokens": completion,
            "total_tokens": total,
        }
    raw = payload.get("raw") or {}
    prompt = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    completion = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    total = int(raw.get("total_tokens") or (prompt + completion))
    details = raw.get("prompt_tokens_details") or {}
    cached = int(
        details.get("cached_tokens")
        or raw.get("prompt_cache_hit_tokens")
        or raw.get("cache_read_input_tokens")
        or 0
    )
    cache_write = int(
        raw.get("cache_creation_input_tokens")
        or details.get("cache_creation_tokens")
        or 0
    )
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "cache_creation_tokens": cache_write,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _record(
    *,
    provider: str,
    model_name: str,
    stage: str,
    tokens: dict[str, int],
) -> None:
    ctx = _usage_ctx.get()
    if ctx is None:
        logger.info(
            "模型用量（无请求上下文，仅日志）: model=%s tokens=%s",
            model_name,
            tokens.get("total_tokens"),
        )
        return
    cost = estimate_cost_micro(
        model_name,
        prompt_tokens=tokens.get("prompt_tokens", 0),
        cached_tokens=tokens.get("cached_tokens", 0),
        completion_tokens=tokens.get("completion_tokens", 0),
        cache_creation_tokens=tokens.get("cache_creation_tokens", 0),
    )
    ctx.append(
        UsageEntry(
            provider=provider or "",
            model_name=model_name or "",
            stage=stage or "llm",
            prompt_tokens=tokens.get("prompt_tokens", 0),
            cached_tokens=tokens.get("cached_tokens", 0),
            completion_tokens=tokens.get("completion_tokens", 0),
            total_tokens=tokens.get("total_tokens", 0),
            cost_micro_yuan=cost,
            billable=(ctx.user_id is not None and (stage or "llm") in ctx.system_stages),
        )
    )


def record_llm_usage(provider: str, model_name: str, usage_payload: dict[str, Any]) -> None:
    """LangChain 回调入口：usage_payload 为 usage_metadata 或原始 token_usage。"""
    tokens = _normalize_usage(usage_payload)
    _record(provider=provider, model_name=model_name, stage="llm", tokens=tokens)


def record_fallback_usage(provider: str, model_name: str, *, stage: str = "llm") -> None:
    """模型调用成功但未拿到 usage 时的兜底：记一条 0 token 记录，按最低 1 点结算。

    真实调用通常会通过回调/原生 usage 上报；只有供应商未返回用量或调用方
    （如测试替身）绕过回调时才走到这里，避免系统托管调用完全免费。
    """
    ctx = current_usage_context()
    if ctx is None or ctx.user_id is None or stage not in ctx.system_stages:
        return
    with ctx.lock:
        if any(entry.billable and entry.stage == stage for entry in ctx.entries):
            return
    _record(provider=provider, model_name=model_name, stage=stage, tokens=_normalize_usage({}))


def record_native_usage(
    provider: str,
    model_name: str,
    usage: dict[str, Any],
    *,
    stage: str = "vision_ocr",
) -> None:
    """原生 OpenAI SDK 调用入口（视觉 OCR 专用，不经 LangChain）。"""
    tokens = _normalize_usage({"raw": usage or {}})
    _record(provider=provider, model_name=model_name, stage=stage, tokens=tokens)


class UsageCallbackHandler(BaseCallbackHandler):
    """LangChain 回调：每次真实模型调用结束后把 usage 写入当前请求上下文。"""

    def __init__(self, provider: str, model_name: str):
        super().__init__()
        self.provider = provider
        self.model_name = model_name

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            payload = _extract_usage_from_llm_result(response)
            if not payload:
                logger.warning("模型调用未返回 usage: model=%s", self.model_name)
                return
            record_llm_usage(self.provider, self.model_name, payload)
        except Exception:  # noqa: BLE001
            logger.exception("记录模型用量失败")

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        logger.warning("模型调用失败: model=%s error=%s", self.model_name, error)


def flush_usage(ctx: UsageContext | None) -> None:
    """请求结束时把用量写入 DB，并按实际费用扣减免费点数。"""
    if ctx is None or ctx.flushed:
        return
    ctx.flushed = True
    with ctx.lock:
        entries = list(ctx.entries)
    if not entries:
        return
    billable_entries = [entry for entry in entries if entry.billable]
    billable_cost = sum(
        entry.cost_micro_yuan for entry in billable_entries if entry.cost_micro_yuan > 0
    )
    # 系统托管调用按实际费用折点，单次最低 1 点；用量未知（0 token）同样按 1 点计。
    points = max(1, points_for_cost(billable_cost)) if billable_entries else 0
    try:
        from db import SessionLocal
        from db.models import ModelUsageEvent

        with SessionLocal() as db:
            quota_date = _today_str()
            try:
                db.add_all(
                    [
                        ModelUsageEvent(
                            user_id=ctx.user_id,
                            provider_source=ctx.provider_source or "",
                            provider=entry.provider,
                            model_name=entry.model_name,
                            action_type=ctx.action_type,
                            run_id=ctx.run_id,
                            prompt_tokens=entry.prompt_tokens,
                            cached_tokens=entry.cached_tokens,
                            completion_tokens=entry.completion_tokens,
                            total_tokens=entry.total_tokens,
                            cost_micro_yuan=entry.cost_micro_yuan,
                            billable=entry.billable,
                            quota_date=quota_date,
                        )
                        for entry in entries
                    ]
                )
                db.flush()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("记录模型用量明细失败（不影响点数扣减）")
            charged = 0
            if ctx.user_id is not None and points > 0:
                from core.quota import charge_usage_points

                token_total = sum(entry.total_tokens for entry in entries)
                charged = charge_usage_points(
                    db,
                    ctx.user_id,
                    points,
                    action_type=ctx.action_type,
                    summary=(
                        f"{ctx.action_type} · {token_total} tokens · "
                        f"¥{billable_cost / _MICRO_PER_YUAN:.4f} · {points} 点"
                    ),
                    quota_date=quota_date,
                )
            db.commit()
            logger.info(
                "用量记录: user=%s action=%s calls=%s tokens=%s cost=¥%.6f points=%s",
                ctx.user_id,
                ctx.action_type,
                len(entries),
                sum(entry.total_tokens for entry in entries),
                billable_cost / _MICRO_PER_YUAN,
                charged,
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入模型用量失败")


def _micro_to_yuan(value: int | None) -> float:
    return round((int(value or 0)) / _MICRO_PER_YUAN, 6)


def get_today_usage(db, user_id: int, quota_date: str) -> dict:
    """当日用量：token、费用、扣点、按模型拆分（额度快照用）。"""
    from db.models import ModelUsageEvent, QuotaUsageEvent
    from sqlalchemy import func

    rows = (
        db.query(
            func.coalesce(func.sum(ModelUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(ModelUsageEvent.cached_tokens), 0),
            func.coalesce(func.sum(ModelUsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(ModelUsageEvent.cost_micro_yuan), 0),
            func.count(ModelUsageEvent.id),
        )
        .filter(
            ModelUsageEvent.user_id == user_id,
            ModelUsageEvent.quota_date == quota_date,
        )
        .one()
    )
    points = (
        db.query(func.coalesce(func.sum(QuotaUsageEvent.amount), 0))
        .filter(
            QuotaUsageEvent.user_id == user_id,
            QuotaUsageEvent.quota_date == quota_date,
        )
        .scalar()
        or 0
    )
    model_rows = (
        db.query(
            ModelUsageEvent.model_name,
            func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(ModelUsageEvent.cost_micro_yuan), 0),
            func.count(ModelUsageEvent.id),
        )
        .filter(
            ModelUsageEvent.user_id == user_id,
            ModelUsageEvent.quota_date == quota_date,
        )
        .group_by(ModelUsageEvent.model_name)
        .all()
    )
    by_model = [
        {
            "model_name": row[0] or "",
            "total_tokens": int(row[1] or 0),
            "cost_yuan": _micro_to_yuan(row[2]),
            "requests": int(row[3] or 0),
        }
        for row in model_rows
    ]
    return {
        "requests": int(rows[5] or 0),
        "prompt_tokens": int(rows[0] or 0),
        "cached_tokens": int(rows[1] or 0),
        "completion_tokens": int(rows[2] or 0),
        "total_tokens": int(rows[3] or 0),
        "cost_yuan": _micro_to_yuan(rows[4]),
        "points": int(points or 0),
        "by_model": by_model,
    }


def get_usage_summary(db, user_id: int | None = None, days: int = 14) -> dict:
    """个人/全局用量汇总。user_id=None 表示管理员全局。"""
    from db.models import ModelUsageEvent, QuotaUsageEvent, User

    normalized_days = max(min(int(days or 14), 90), 1)
    start_date = datetime.utcnow().date() - timedelta(days=normalized_days - 1)
    start_dt = datetime.combine(start_date, time.min)

    query = db.query(ModelUsageEvent).filter(ModelUsageEvent.created_at >= start_dt)
    if user_id is not None:
        query = query.filter(ModelUsageEvent.user_id == user_id)
    events = query.order_by(ModelUsageEvent.created_at.desc()).all()

    total = {
        "requests": len(events),
        "prompt_tokens": sum(event.prompt_tokens for event in events),
        "cached_tokens": sum(event.cached_tokens for event in events),
        "completion_tokens": sum(event.completion_tokens for event in events),
        "total_tokens": sum(event.total_tokens for event in events),
        "cost_yuan": _micro_to_yuan(sum(event.cost_micro_yuan for event in events)),
        "points": 0,
    }

    quota_query = db.query(QuotaUsageEvent).filter(
        QuotaUsageEvent.quota_date >= start_date.isoformat()
    )
    if user_id is not None:
        quota_query = quota_query.filter(QuotaUsageEvent.user_id == user_id)
    quota_events = quota_query.all()
    points_by_action: dict[str, int] = {}
    points_by_day: dict[str, int] = {}
    for event in quota_events:
        points_by_action[event.action_type] = points_by_action.get(event.action_type, 0) + int(
            event.amount or 0
        )
        points_by_day[event.quota_date] = points_by_day.get(event.quota_date, 0) + int(
            event.amount or 0
        )
    total["points"] = sum(points_by_action.values())

    by_model_map: dict[str, dict] = {}
    by_action_map: dict[str, dict] = {}
    daily_map: dict[str, dict] = {}
    for event in events:
        date_key = (event.created_at or datetime.utcnow()).date().isoformat()
        for target, key in (
            (by_model_map, event.model_name or "unknown"),
            (by_action_map, event.action_type or "other"),
        ):
            item = target.setdefault(
                key,
                {"requests": 0, "total_tokens": 0, "cost_micro": 0, "points": 0},
            )
            item["requests"] += 1
            item["total_tokens"] += event.total_tokens
            item["cost_micro"] += event.cost_micro_yuan
        day_item = daily_map.setdefault(
            date_key, {"requests": 0, "total_tokens": 0, "cost_micro": 0, "points": 0}
        )
        day_item["requests"] += 1
        day_item["total_tokens"] += event.total_tokens
        day_item["cost_micro"] += event.cost_micro_yuan

    from core.quota import QUOTA_ACTION_META

    by_model = sorted(
        [
            {
                "model_name": key,
                "requests": item["requests"],
                "total_tokens": item["total_tokens"],
                "cost_yuan": _micro_to_yuan(item["cost_micro"]),
                "points": 0,
            }
            for key, item in by_model_map.items()
        ],
        key=lambda item: item["total_tokens"],
        reverse=True,
    )

    by_action = []
    for key, item in by_action_map.items():
        meta = QUOTA_ACTION_META.get(key, QUOTA_ACTION_META.get("other", {}))
        by_action.append(
            {
                "action_type": key,
                "label": meta.get("label", key),
                "requests": item["requests"],
                "total_tokens": item["total_tokens"],
                "cost_yuan": _micro_to_yuan(item["cost_micro"]),
                "points": int(points_by_action.get(key, 0)),
            }
        )
    by_action.sort(key=lambda item: item["points"], reverse=True)

    daily = []
    cursor = start_date
    while cursor <= datetime.utcnow().date():
        date_key = cursor.isoformat()
        item = daily_map.get(date_key, {"requests": 0, "total_tokens": 0, "cost_micro": 0})
        daily.append(
            {
                "date": date_key,
                "label": cursor.strftime("%m/%d"),
                "requests": item["requests"],
                "total_tokens": item["total_tokens"],
                "cost_yuan": _micro_to_yuan(item["cost_micro"]),
                "points": int(points_by_day.get(date_key, 0)),
            }
        )
        cursor += timedelta(days=1)

    recent = [
        {
            "created_at": event.created_at.isoformat() + "Z" if event.created_at else None,
            "model_name": event.model_name,
            "action_type": event.action_type,
            "provider_source": event.provider_source,
            "prompt_tokens": event.prompt_tokens,
            "cached_tokens": event.cached_tokens,
            "completion_tokens": event.completion_tokens,
            "total_tokens": event.total_tokens,
            "cost_yuan": _micro_to_yuan(event.cost_micro_yuan),
            "points": points_for_cost(event.cost_micro_yuan) if event.billable else 0,
            "billable": bool(event.billable),
        }
        for event in events[:20]
    ]

    result = {
        "days": normalized_days,
        "total": total,
        "by_model": by_model,
        "by_action": by_action,
        "daily": daily,
        "recent": recent,
    }

    if user_id is None:
        user_ids = {event.user_id for event in events if event.user_id is not None}
        users = (
            db.query(User).filter(User.id.in_(user_ids)).all()
            if user_ids
            else []
        )
        name_map = {user.id: (user.username or user.email or str(user.id)) for user in users}
        by_user: dict[int, dict] = {}
        for event in events:
            if event.user_id is None:
                continue
            item = by_user.setdefault(
                event.user_id, {"requests": 0, "total_tokens": 0, "cost_micro": 0}
            )
            item["requests"] += 1
            item["total_tokens"] += event.total_tokens
            item["cost_micro"] += event.cost_micro_yuan
        points_by_user: dict[int, int] = {}
        for event in quota_events:
            points_by_user[event.user_id] = points_by_user.get(event.user_id, 0) + int(
                event.amount or 0
            )
        result["by_user"] = sorted(
            [
                {
                    "user_id": uid,
                    "username": name_map.get(uid, str(uid)),
                    "requests": item["requests"],
                    "total_tokens": item["total_tokens"],
                    "cost_yuan": _micro_to_yuan(item["cost_micro"]),
                    "points": int(points_by_user.get(uid, 0)),
                }
                for uid, item in by_user.items()
            ],
            key=lambda item: item["total_tokens"],
            reverse=True,
        )
        result["users_count"] = len(by_user)

    return result
