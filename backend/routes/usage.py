"""模型用量统计接口：用户个人用量与管理员全局用量。"""

import logging

from flask import Blueprint, jsonify, request, session

from db import SessionLocal
from db import crud
from core.usage import get_usage_summary

logger = logging.getLogger(__name__)

bp = Blueprint("usage", __name__)


def _current_user(db):
    uid = session.get("user_id")
    if not uid:
        return None
    return crud.get_user_by_id(db, uid)


def _parse_days() -> int:
    days = request.args.get("days", default=14, type=int) or 14
    return max(min(int(days), 90), 1)


@bp.route("/usage/summary", methods=["GET"])
def get_my_usage_summary():
    """当前用户近 N 天的 token/费用/点数用量。"""
    try:
        with SessionLocal() as db:
            user = _current_user(db)
            if user is None:
                return jsonify({"success": False, "error": "未登录或登录已失效"}), 401
            summary = get_usage_summary(db, user_id=user.id, days=_parse_days())
            return jsonify(
                {
                    "success": True,
                    "scope": "user",
                    **summary,
                    "daily_free_quota": user.daily_free_quota,
                    "daily_free_used": user.daily_free_used,
                }
            )
    except Exception:
        logger.exception("获取个人用量失败")
        return jsonify({"success": False, "error": "获取用量失败，请稍后重试"}), 500


@bp.route("/admin/usage/summary", methods=["GET"])
def get_global_usage_summary():
    """管理员查看全站近 N 天的 token/费用/点数用量与按用户汇总。"""
    try:
        with SessionLocal() as db:
            user = _current_user(db)
            if user is None:
                return jsonify({"success": False, "error": "未登录或登录已失效"}), 401
            if not getattr(user, "is_admin", False):
                return jsonify({"success": False, "error": "需要管理员权限"}), 403
            summary = get_usage_summary(db, user_id=None, days=_parse_days())
            return jsonify({"success": True, "scope": "global", **summary})
    except Exception:
        logger.exception("获取全局用量失败")
        return jsonify({"success": False, "error": "获取用量失败，请稍后重试"}), 500
