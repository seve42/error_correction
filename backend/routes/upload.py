import os
import json
import time
import uuid
import logging
from datetime import timezone
from pathlib import PurePath
from typing import Optional

from flask import Blueprint, Response, request, jsonify, session

import core.state as state
from core.state import (
    workflow_graph,
    session_lock,
    get_user_session,
    ordered_file_keys,
)
from db import SessionLocal
from db import crud
from core.config import settings
from core.model_selection import LLMSelectionError, resolve_llm_selection
from core import workflow_run_store as run_store
from core.quota import (
    has_daily_free_quota,
    quota_exceeded_response,
    uses_server_llm_selection,
    uses_server_ocr,
)
from pipeline.utils import export_wrongbook as export_wrongbook_md
from core.task_progress import (
    EventEmitter,
    PLACEHOLDER_WAIT_SECONDS,
    STREAM_KEEPALIVE_SECONDS,
    TaskAccessError,
    ensure_task,
    is_valid_task_id,
    snapshot,
    wait_events,
)

logger = logging.getLogger(__name__)

bp = Blueprint("upload", __name__)


# ---------------------------------------------------------------------------
# 进度任务（SSE）辅助
# ---------------------------------------------------------------------------


def _parse_task_id(payload: dict) -> Optional[str]:
    """从请求体读取可选的 task_id（前端 crypto.randomUUID 生成）。"""
    raw = payload.get("task_id")
    if raw is None or raw == "":
        return None
    value = str(raw).strip()
    if not is_valid_task_id(value):
        raise ValueError("task_id 非法")
    return value


def _start_progress(task_id: Optional[str], user_id, kind: str) -> EventEmitter:
    """登记进度任务并返回事件出口。

    同一 task_id 会被前端连续用于 OCR 与分割两个阶段：POST 时 restart=True 只清
    上一阶段的完成标记，历史事件与 seq 保留，前端按 after=上一个 seq 续连即可拿到
    新阶段事件。task_id 为空时返回 no-op emitter，进度事件不影响主流程。
    """
    if not task_id:
        return EventEmitter(None)
    ensure_task(task_id, user_id, kind=kind, restart=True)
    return EventEmitter(task_id)


def _finish_ok(emitter: EventEmitter, message: str) -> None:
    """成功收尾：先发 success 事件再结束任务，轮询兜底也能读到最终文案。"""
    emitter.emit(step="done", stage="完成", level="success", message=message, progress=1.0)
    emitter.finish(ok=True, message=message)


def _finish_failed(emitter: EventEmitter, message: str) -> None:
    """失败收尾：错误事件 + 结束标记，前端无需等到超时。"""
    emitter.emit(step="done", stage="完成", level="error", message=message)
    emitter.finish(ok=False, message=message)



# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return PurePath(filename).suffix.lower().lstrip(".") in settings.allowed_extensions


def _read_split_subject(result_dir: str | None = None) -> Optional[str]:
    """从 split_metadata.json 读取学科信息"""
    meta_path = os.path.join(str(result_dir or settings.results_dir), "split_metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("subject")
    return None


def _isoformat_utc(dt) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _is_image_file(path: str) -> bool:
    return PurePath(path).suffix.lower().lstrip(".") in {
        "jpg",
        "jpeg",
        "png",
        "webp",
        "bmp",
    }


def _build_original_image_payload(session_files: list[dict]) -> list[dict]:
    images = []
    for item in session_files:
        filename = item.get("filename") or "Unknown"
        filepath = item.get("filepath") or ""
        stored_name = os.path.basename(filepath) if filepath else ""
        is_image = bool(filepath and _is_image_file(filepath))
        image_url = f"/api/image/{stored_name}" if is_image and stored_name else None
        images.append(
            {
                "filename": filename,
                "stored_name": stored_name,
                "url": image_url,
                "image_url": image_url,
                "is_image": is_image,
            }
        )
    return images


def _serialize_split_record(r) -> dict:
    """将 SplitRecord ORM 对象序列化为前端 JSON 格式"""
    original_images = (
        json.loads(r.original_images_json)
        if getattr(r, "original_images_json", None)
        else []
    )
    return {
        "id": r.id,
        "subject": r.subject,
        "model_provider": r.model_provider,
        "file_names": json.loads(r.file_names_json) if r.file_names_json else [],
        "original_images": original_images,
        "question_count": r.question_count,
        "created_at": _isoformat_utc(r.created_at),
    }


def _apply_provider_context(db, user_id: Optional[int]) -> None:
    managed_llm = {}
    for category in ("openai", "anthropic"):
        provider = crud.get_active_system_provider(db, category)
        if provider and provider.api_key:
            managed_llm[category] = settings.build_provider_config(
                category,
                api_key=provider.api_key or "",
                base_url=provider.base_url or "",
                model_name=provider.model_name or None,
                light_model_name=getattr(provider, "light_model_name", None) or None,
                supports_function_calling=getattr(
                    provider, "supports_function_calling", True
                ),
            )

    ocr_provider = crud.get_active_system_provider(db, "paddleocr")
    managed_ocr = (
        settings.build_ocr_config(
            api_url=ocr_provider.base_url or "",
            token=ocr_provider.api_key or "",
            model=ocr_provider.model_name or "PaddleOCR-VL-1.5",
            use_doc_orientation=getattr(ocr_provider, "use_doc_orientation", False),
            use_doc_unwarping=getattr(ocr_provider, "use_doc_unwarping", False),
            use_chart_recognition=getattr(ocr_provider, "use_chart_recognition", False),
        )
        if ocr_provider
        else {}
    )

    settings.apply_managed_providers(managed_llm, managed_ocr)
    if user_id:
        settings.load_providers_from_db(user_id, db=db)
    else:
        settings.reload_providers()


def _ocr_cache_key(user_id: Optional[int]) -> str:
    return str(user_id) if user_id is not None else "anon"


def _ocr_cache_path(user_id: Optional[int]) -> str:
    return os.path.join(
        str(settings.results_dir), f"ocr_cache_{_ocr_cache_key(user_id)}.json"
    )


def _vision_cache_path(user_id: Optional[int]) -> str:
    return os.path.join(
        str(settings.results_dir), f"ocr_cache_{_ocr_cache_key(user_id)}_vision.json"
    )


def _cache_path(engine: str, user_id: Optional[int]) -> str:
    return _vision_cache_path(user_id) if engine == "vision" else _ocr_cache_path(user_id)


def _clear_ocr_cache(user_id: Optional[int]) -> None:
    for cache_path in (_ocr_cache_path(user_id), _vision_cache_path(user_id)):
        try:
            os.remove(cache_path)
        except FileNotFoundError:
            pass


def _mark_server_ocr_cache(user_id: Optional[int], value: bool) -> None:
    with session_lock:
        us = get_user_session(user_id)
        us["ocr_cache_uses_server_ocr"] = bool(value)


def _has_server_ocr_cache(user_id: Optional[int]) -> bool:
    with session_lock:
        cached = bool(get_user_session(user_id).get("ocr_cache_uses_server_ocr"))
    return cached and os.path.exists(_ocr_cache_path(user_id))


def _clear_upload_runtime_state(user_id: Optional[int]) -> None:
    """清理当前用户上传链路的会话态与临时文件。"""
    with session_lock:
        us = get_user_session(user_id)
        session_files = list(us.get("session_files", {}).values())
        erased_files = list(us.get("erased_file_paths") or [])
        us["current_thread_id"] = None
        us["session_files"].clear()
        us["session_file_order"].clear()
        us["cancelled_file_keys"].clear()
        us["erased_file_paths"] = None
        us["ocr_cache_uses_server_ocr"] = False

    for item in session_files:
        fp = (item or {}).get("filepath")
        if not fp:
            continue
        try:
            os.remove(fp)
        except FileNotFoundError:
            pass

    for fp in erased_files:
        if not fp:
            continue
        try:
            os.remove(fp)
        except FileNotFoundError:
            pass

    _clear_ocr_cache(user_id)


def _is_pdf_file(file_path: str) -> bool:
    return PurePath(file_path).suffix.lower() == ".pdf"


def _expand_erase_sources(file_paths: list[str]) -> list[dict]:
    """将擦除输入展开为按页图片，确保 PDF 擦除和扣费都按页处理。"""
    expanded_sources = []
    os.makedirs(str(settings.pages_dir), exist_ok=True)

    for file_path in file_paths:
        if not _is_pdf_file(file_path):
            expanded_sources.append(
                {
                    "input_path": file_path,
                    "preview_path": file_path,
                }
            )
            continue

        try:
            from pdf2image import convert_from_path

            page_images = convert_from_path(str(file_path), fmt="png")
        except Exception as exc:
            logger.warning("PDF 擦除预处理失败: %s", file_path, exc_info=True)
            raise ValueError("PDF 擦除失败，当前环境缺少 PDF 转图片支持") from exc

        if not page_images:
            raise ValueError("PDF 不包含可处理的页面")

        file_stem = PurePath(file_path).stem
        for index, page_image in enumerate(page_images, start=1):
            rendered_name = f"erase_source_{uuid.uuid4().hex[:8]}_{file_stem}_{index}.png"
            rendered_path = os.path.join(str(settings.pages_dir), rendered_name)
            page_image.save(rendered_path, format="PNG")
            expanded_sources.append(
                {
                    "input_path": rendered_path,
                    "preview_path": rendered_path,
                }
            )

    return expanded_sources


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/upload", methods=["POST"])
def upload_file():
    """处理文件上传（支持多文件）。

    这里只负责把原始文件保存到 uploads 并登记到会话中；
    标准化(PDF/图片 → 图片列表) + OCR + 分割，会在 /api/split 里由用户点击"开始分割"后统一触发。

    Returns:
        JSON响应，包含上传结果
    """
    # 支持多文件：前端用 'files' 字段发送，兼容单文件 'file' 字段
    files = request.files.getlist("files")
    if not files:
        files = request.files.getlist("file")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "没有上传文件"}), 400

    # 校验每个文件
    for file in files:
        if file.filename == "":
            continue

        if not allowed_file(file.filename):
            return (
                jsonify(
                    {
                        "error": f'不支持的文件格式: {file.filename}。支持: {", ".join(settings.allowed_extensions)}'
                    }
                ),
                400,
            )

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        file_size_mb = file_size / (1024 * 1024)

        if file_size_mb > settings.max_file_size_mb:
            return (
                jsonify(
                    {
                        "error": f"{file.filename} 大小为 {file_size_mb:.1f}MB，超出最大限制 {settings.max_file_size_mb}MB"
                    }
                ),
                400,
            )

        if file_size == 0:
            return jsonify({"error": f"{file.filename} 为空文件，请重新选择"}), 400

    try:
        should_reset_session = str(
            request.form.get("reset_session", "")
        ).strip().lower() in ("1", "true", "yes", "on")
        file_keys = request.form.getlist("file_key")
        if not file_keys:
            file_keys = request.form.getlist("file_keys")
        # 前端并发分批上传时，请求到达顺序与用户选择顺序无关，
        # 所以每个文件额外带上它在本次选择中的全局序号，供后续按序识别。
        file_orders = request.form.getlist("file_order")

        prepared = []
        for i, file in enumerate(files):
            if file.filename == "":
                continue
            fk = file_keys[i] if i < len(file_keys) and file_keys[i] else None
            order = file_orders[i] if i < len(file_orders) and file_orders[i] else None
            prepared.append((fk, order, file))

        if not prepared:
            return jsonify({"error": "没有上传文件"}), 400

        results_out = []
        uid = session.get("user_id")
        if should_reset_session:
            _clear_upload_runtime_state(uid)
        for fk, order, file in prepared:
            file_key = fk or f"{uuid.uuid4().hex}"

            original_ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
            filename = f"{uuid.uuid4().hex}.{original_ext}"
            filepath = os.path.join(str(settings.upload_dir), filename)
            file.save(filepath)

            with session_lock:
                us = get_user_session(uid)
                cancelled = file_key in us["cancelled_file_keys"]
                if cancelled:
                    us["cancelled_file_keys"].discard(file_key)

            if cancelled:
                try:
                    os.remove(filepath)
                except FileNotFoundError:
                    pass
                continue

            with session_lock:
                us = get_user_session(uid)
                if us["current_thread_id"] is not None:
                    us["current_thread_id"] = None
                    us["session_files"].clear()
                    us["session_file_order"].clear()
                    us["ocr_cache_uses_server_ocr"] = False
                    _clear_ocr_cache(uid)

                us["session_files"][file_key] = {
                    "filename": file.filename,
                    "filepath": filepath,
                    "order": int(order) if order and str(order).isdigit() else None,
                }
                if file_key not in us["session_file_order"]:
                    us["session_file_order"].append(file_key)
                us["ocr_cache_uses_server_ocr"] = False
                _clear_ocr_cache(uid)

            results_out.append(
                {
                    "file_key": file_key,
                    "filename": file.filename,
                }
            )

        return jsonify(
            {
                "success": True,
                "message": "上传成功",
                "result": {
                    "file_count": len(results_out),
                    "files": results_out,
                },
            }
        )

    except FileNotFoundError:
        return jsonify({"success": False, "error": "文件保存失败，请重新上传"}), 500
    except Exception as e:
        logger.exception("文件处理失败")
        return jsonify({"success": False, "error": "文件处理失败，请稍后重试"}), 500


@bp.route("/cancel_file", methods=["POST"])
def cancel_file():
    try:
        data = request.get_json(silent=True) or {}
        file_key = data.get("file_key")
        if not file_key:
            return jsonify({"success": False, "error": "缺少 file_key"}), 400

        uid = session.get("user_id")
        with session_lock:
            us = get_user_session(uid)
            if us["current_thread_id"] is not None:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "已开始分割，无法撤销单个文件；请重置后重新上传",
                        }
                    ),
                    400,
                )

        filepath = None
        existed = False
        with session_lock:
            us = get_user_session(uid)
            us["cancelled_file_keys"].add(file_key)

            existed = file_key in us["session_files"]
            v = us["session_files"].pop(file_key, None) or {}
            filepath = v.get("filepath")
            if file_key in us["session_file_order"]:
                us["session_file_order"].remove(file_key)
            us["ocr_cache_uses_server_ocr"] = False
            _clear_ocr_cache(uid)

        if filepath:
            try:
                os.remove(filepath)
            except FileNotFoundError:
                pass

        return jsonify(
            {
                "success": True,
                "message": "已撤销该文件" if existed else "已标记撤销该文件",
            }
        )

    except Exception as e:
        logger.exception("撤销文件失败")
        return jsonify({"success": False, "error": "撤销失败，请稍后重试"}), 500


@bp.route("/upload/reset", methods=["POST"])
def reset_upload_session():
    """重置上传流程会话状态，避免历史文件串入下一次上传。"""
    try:
        uid = session.get("user_id")
        _clear_upload_runtime_state(uid)
        return jsonify(
            {
                "success": True,
                "message": "上传会话已重置",
            }
        )
    except Exception:
        logger.exception("重置上传会话失败")
        return jsonify({"success": False, "error": "重置失败，请稍后重试"}), 500


@bp.route("/erase", methods=["POST"])
def run_erase():
    """擦除手写笔迹，返回擦除后的图片 URL。

    擦除后的文件路径保存在会话状态中，供后续 /api/ocr 使用。
    """
    try:
        uid = session.get("user_id")
        with session_lock:
            us = get_user_session(uid)
            keys = ordered_file_keys(us)
            file_paths = []
            for k in keys:
                v = us["session_files"].get(k) or {}
                fp = v.get("filepath")
                if fp:
                    file_paths.append(fp)

        if not file_paths:
            return jsonify({"success": False, "error": "请先上传文件"}), 400

        erase_sources = _expand_erase_sources(file_paths)

        # 本地文本擦除不调用模型，不消耗免费点数。
        ensexam_configured = settings.model_path.exists()
        if not ensexam_configured:
            return jsonify({"success": False, "error": "EnsExam 模型未配置"}), 400

        try:
            from text_eraser_model.inference import InferenceEngine
        except ModuleNotFoundError as exc:
            if exc.name == "torch":
                logger.exception("EnsExam 擦除模型依赖缺失")
                return jsonify({
                    "success": False,
                    "code": "ERASER_DEPENDENCY_MISSING",
                    "error": "擦除模型依赖缺失：请在后端环境安装 PyTorch 后重试",
                }), 503
            raise

        engine = InferenceEngine()
        erased_paths = []
        for source in erase_sources:
            with open(source["input_path"], "rb") as f:
                img_bytes = f.read()
            result_img = engine.run(img_bytes)
            erased_name = f"auto_{uuid.uuid4().hex[:8]}_{os.path.splitext(os.path.basename(source['input_path']))[0]}.png"
            erased_path = settings.erased_dir / erased_name
            result_img.save(str(erased_path), format="PNG")
            erased_paths.append(str(erased_path))

        # 保存擦除后的路径到会话，供 /api/ocr 复用
        with session_lock:
            us = get_user_session(uid)
            us["erased_file_paths"] = erased_paths
            us["ocr_cache_uses_server_ocr"] = False
        _clear_ocr_cache(uid)

        preview = []
        for i, (source, erased) in enumerate(zip(erase_sources, erased_paths)):
            preview.append(
                {
                    "index": i,
                    "original_url": f"/api/image/{os.path.basename(source['preview_path'])}",
                    "erased_url": f"/api/image/{os.path.basename(erased)}",
                }
            )

        return jsonify(
            {
                "success": True,
                "message": f"擦除完成，共 {len(erased_paths)} 张图片",
                "images": preview,
            }
        )

    except RuntimeError as exc:
        if "Git LFS 指针文件" in str(exc):
            logger.exception("EnsExam 擦除模型权重未下载")
            return jsonify({
                "success": False,
                "code": "ERASER_MODEL_WEIGHT_POINTER",
                "error": "擦除模型权重未下载：请在构建或部署环境执行 git lfs pull 后重试",
            }), 503
        logger.exception("擦除手写笔迹失败")
        return jsonify({"success": False, "error": "擦除失败，请稍后重试"}), 500
    except Exception:
        logger.exception("擦除手写笔迹失败")
        return jsonify({"success": False, "error": "擦除失败，请稍后重试"}), 500


@bp.route("/ocr", methods=["POST"])
def run_ocr():
    """只执行 OCR，返回识别结果供用户预览。

    流程：标准化输入 → OCR 解析 → 返回结构化文本
    不触发 Agent 分割。
    如果之前调用过 /api/erase，则使用擦除后的图片。
    """
    emitter = EventEmitter(None)
    try:
        # 加载 OCR 凭据
        user_id = session.get("user_id")
        request_data = request.get_json(silent=True) or {}
        engine = str(request_data.get("engine") or "paddleocr").strip().lower()
        if engine not in ("vision", "paddleocr"):
            engine = "paddleocr"

        # 进度任务必须在耗时工作之前登记：前端可能先连 SSE 再发 POST，
        # 事件早于 SSE 也不会丢，因为订阅方按 seq 回放已有事件。
        try:
            task_id = _parse_task_id(request_data)
        except ValueError:
            return jsonify({"success": False, "error": "task_id 非法", "code": "INVALID_TASK_ID"}), 400
        try:
            emitter = _start_progress(task_id, user_id, "ocr")
        except TaskAccessError:
            return jsonify({"success": False, "error": "任务不存在"}), 403
        emitter.emit(step="prepare", stage="准备", message="正在准备视觉识别任务…")

        ocr_credentials = {}
        vision_credentials = None
        _mark_server_ocr_cache(user_id, False)
        with SessionLocal() as db:
            _apply_provider_context(db, user_id)
            if engine == "vision":
                from pipeline.vision_ocr_client import resolve_vision_credentials

                vision_credentials = resolve_vision_credentials(db, user_id)
                if not vision_credentials:
                    logger.warning("未配置支持图像理解的 Provider，/api/ocr 回退 PaddleOCR")
                    emitter.emit(
                        step="prepare",
                        stage="准备",
                        level="warn",
                        message="未配置支持图像理解的模型，本次回退 PaddleOCR 识别",
                    )
                    engine = "paddleocr"
            if user_id:
                ocr_provider = crud.get_active_provider(db, user_id, "paddleocr")
                if ocr_provider:
                    ocr_credentials = {
                        "api_url": ocr_provider.base_url,
                        "token": ocr_provider.api_key,
                        "model": ocr_provider.model_name,
                        "use_doc_orientation": ocr_provider.use_doc_orientation,
                        "use_doc_unwarping": ocr_provider.use_doc_unwarping,
                        "use_chart_recognition": ocr_provider.use_chart_recognition,
                    }

        # 优先使用擦除后的路径（视觉引擎直接读原图，不使用擦除图）
        file_paths = None
        with session_lock:
            us = get_user_session(user_id)
            if engine != "vision" and us["erased_file_paths"]:
                file_paths = list(us["erased_file_paths"])
                us["erased_file_paths"] = None  # 用完即清

        if not file_paths:
            with session_lock:
                us = get_user_session(user_id)
                keys = ordered_file_keys(us)
                file_paths = []
                for k in keys:
                    v = us["session_files"].get(k) or {}
                    fp = v.get("filepath")
                    if fp:
                        file_paths.append(fp)

        if not file_paths:
            _finish_failed(emitter, "请先上传文件")
            return jsonify({"success": False, "error": "请先上传文件"}), 400

        # 标准化输入（PDF → 图片）
        from pipeline.utils import prepare_input

        all_image_paths = []
        for fp in file_paths:
            all_image_paths.extend(prepare_input(fp))
        emitter.emit(
            step="prepare",
            stage="准备",
            level="success",
            # prepare_input 只做格式标准化与落盘，PDF 不在此处拆页，
            # 所以这里报「文件数」而不是页数，真实页数由识别阶段（vision_load）上报。
            message=f"已准备 {len(all_image_paths)} 个输入文件，开始识别…",
            progress=1.0,
        )

        # ── 视觉 OCR：deepseek-flash 直接读原图，输出带笔迹/批改标签的结构化结果 ──
        if engine == "vision":
            from pipeline.vision_ocr_client import VisionOCRClient
            from core.usage import start_usage, end_usage, record_fallback_usage

            vision_is_system = vision_credentials.get("source") == "system"
            if user_id and vision_is_system:
                with SessionLocal() as db:
                    quota_user = crud.get_user_by_id(db, user_id)
                    if quota_user and not has_daily_free_quota(db, quota_user, amount=1):
                        payload, status = quota_exceeded_response(db, quota_user, amount=1)
                        _finish_failed(emitter, payload.get("error") or "今日免费额度已用完")
                        return jsonify(payload), status
            # 知识点标签池：视觉调用早于科目识别，只能给全量标签供模型自行匹配复用
            try:
                with SessionLocal() as db:
                    existing_tags = ",".join(crud.get_existing_tag_names(db))
            except Exception as exc:  # noqa: BLE001
                logger.warning("加载已有知识点标签失败（不影响打标）: %s", exc)
                existing_tags = ""
            vision_client = VisionOCRClient(
                api_key=vision_credentials["api_key"],
                base_url=vision_credentials.get("base_url") or "",
                model=vision_credentials.get("model") or "deepseek-flash",
            )
            usage_ctx = start_usage(
                user_id,
                "ocr",
                provider_source=vision_credentials.get("source") or "",
                system_stages={"vision_ocr"} if vision_is_system else set(),
            )
            try:
                result = vision_client.parse_document(
                    all_image_paths,
                    output_dir=str(settings.pages_dir),
                    emitter=emitter,
                    existing_tags=existing_tags,
                )
                if vision_is_system and user_id:
                    record_fallback_usage(
                        "openai", vision_client.model, stage="vision_ocr"
                    )
            finally:
                end_usage(usage_ctx)
            results_dir = str(settings.results_dir)
            os.makedirs(results_dir, exist_ok=True)
            with open(_vision_cache_path(user_id), "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            _mark_server_ocr_cache(user_id, vision_credentials.get("source") == "system")

            preview_pages = []
            for page in result.get("pages") or []:
                image_name = os.path.basename(page.get("image_path") or "")
                preview_pages.append(
                    {
                        "page_index": page.get("page_index", 0),
                        "image_url": f"/api/image/{image_name}" if image_name else None,
                        "page_width": page.get("width") or 1,
                        "page_height": page.get("height") or 1,
                        "blocks": page.get("blocks") or [],
                    }
                )
            _finish_ok(emitter, f"视觉识别完成，共 {len(preview_pages)} 页")
            return jsonify(
                {
                    "success": True,
                    "engine": "vision",
                    "message": f"视觉识别完成，共 {len(preview_pages)} 页",
                    "pages": preview_pages,
                    "total_blocks": sum(len(p["blocks"]) for p in preview_pages),
                    "tagged_text": result.get("tagged_text") or "",
                }
            )

        # 执行 OCR（获取原始结果用于预览 bbox，简化结果用于后续分割）
        from pipeline.paddleocr_client import PaddleOCRClient
        from pipeline.utils import simplify_ocr_results
        from pipeline.workflow import run_async

        emitter.emit(
            step="vision_batch",
            stage="视觉识别",
            message=f"正在使用 PaddleOCR 识别 {len(all_image_paths)} 页图片…",
        )

        creds = ocr_credentials or {}
        client = PaddleOCRClient(
            api_url=creds.get("api_url"),
            token=creds.get("token"),
            model=creds.get("model"),
            use_doc_orientation=creds.get("use_doc_orientation"),
            use_doc_unwarping=creds.get("use_doc_unwarping"),
            use_chart_recognition=creds.get("use_chart_recognition"),
        )

        # 按文件类型分组
        pdf_paths = [p for p in all_image_paths if p.lower().endswith(".pdf")]
        image_only = [p for p in all_image_paths if not p.lower().endswith(".pdf")]

        raw_ocr_results = []
        for pdf_path in pdf_paths:
            try:
                raw_ocr_results.append(client.parse_pdf(pdf_path, save_output=True))
            except Exception:
                logger.exception(f"OCR PDF 失败: {pdf_path}")
        if image_only:
            try:
                img_results = run_async(
                    client.parse_images_async(image_only, save_output=True)
                )
                if img_results:
                    raw_ocr_results.extend(img_results)
            except Exception:
                logger.exception("OCR 图片失败")

        if not raw_ocr_results:
            _finish_failed(emitter, "OCR 解析失败，请检查 PaddleOCR 配置")
            return (
                jsonify(
                    {"success": False, "error": "OCR 解析失败，请检查 PaddleOCR 配置"}
                ),
                500,
            )

        # 简化结果用于后续分割
        ocr_data = simplify_ocr_results(raw_ocr_results)

        # 保存 OCR 结果到会话，供后续 /api/split 复用
        results_dir = str(settings.results_dir)
        os.makedirs(results_dir, exist_ok=True)
        ocr_cache_path = _ocr_cache_path(user_id)
        with open(ocr_cache_path, "w", encoding="utf-8") as f:
            json.dump(ocr_data, f, ensure_ascii=False, indent=2)
        _mark_server_ocr_cache(user_id, False)

        # 构建前端预览数据：每页的图片 + bbox 标注
        from pathlib import Path

        preview_pages = []
        page_idx = 0
        source_inputs = [*pdf_paths, *image_only]
        for source_idx, result in enumerate(raw_ocr_results):
            source_path = (
                source_inputs[source_idx] if source_idx < len(source_inputs) else None
            )
            source_name = Path(source_path).stem if source_path else None
            is_pdf_source = bool(
                source_path and str(source_path).lower().endswith(".pdf")
            )
            for layout_idx, layout_page in enumerate(
                result.get("layoutParsingResults", [])
            ):
                pruned = layout_page.get("prunedResult", {})
                page_w = pruned.get("width", 1)
                page_h = pruned.get("height", 1)
                parsing_list = pruned.get("parsing_res_list", [])

                blocks = []
                for b in parsing_list:
                    bbox = b.get("block_bbox")
                    if not bbox:
                        continue
                    blocks.append(
                        {
                            "label": b.get("block_label", ""),
                            "content": (b.get("block_content", "") or "")[:80],
                            "bbox": bbox,
                        }
                    )

                image_url = None
                # 图片输入优先显示原图；PDF 优先尝试 OCR 产出的页面图（_save_images 保存到 struct_dir）。
                if source_path and not is_pdf_source and layout_idx == 0:
                    image_url = f"/api/image/{os.path.basename(source_path)}"
                else:
                    output_images = layout_page.get("outputImages", {}) or {}
                    if source_name and output_images:
                        first_key = next(iter(output_images.keys()), None)
                        if first_key:
                            rendered_name = (
                                f"{first_key}_{source_name}_{layout_idx}.jpg"
                            )
                            rendered_path = os.path.join(
                                str(settings.struct_dir), rendered_name
                            )
                            if os.path.exists(rendered_path):
                                image_url = f"/api/image/{rendered_name}"

                if not image_url and page_idx < len(all_image_paths):
                    fallback_path = all_image_paths[page_idx]
                    if not str(fallback_path).lower().endswith(".pdf"):
                        image_url = f"/api/image/{os.path.basename(fallback_path)}"

                preview_pages.append(
                    {
                        "page_index": page_idx,
                        "image_url": image_url,
                        "page_width": page_w,
                        "page_height": page_h,
                        "blocks": blocks,
                    }
                )
                page_idx += 1

        _finish_ok(emitter, f"OCR 完成，共 {len(preview_pages)} 页")
        return jsonify(
            {
                "success": True,
                "message": f"OCR 完成，共 {len(preview_pages)} 页",
                "pages": preview_pages,
                "total_blocks": sum(len(p["blocks"]) for p in preview_pages),
            }
        )

    except Exception:
        logger.exception("OCR 执行失败")
        _finish_failed(emitter, "OCR 执行失败，请稍后重试")
        return jsonify({"success": False, "error": "OCR 执行失败，请稍后重试"}), 500


# ---------------------------------------------------------------------------
# 进度订阅：SSE 主通道 + 轮询兜底
# ---------------------------------------------------------------------------


@bp.route("/task/<task_id>/stream", methods=["GET"])
def stream_task_progress(task_id):
    """推送某个任务的实时进度事件（text/event-stream）。"""
    if not is_valid_task_id(task_id):
        return jsonify({"success": False, "error": "task_id 非法"}), 400
    user_id = session.get("user_id")
    try:
        # 前端往往先连 SSE 再发 POST：先建占位任务，POST 到达前只发心跳。
        ensure_task(task_id, user_id, kind="stream")
    except TaskAccessError:
        return jsonify({"success": False, "error": "任务不存在"}), 403
    after = request.args.get("after", 0, type=int) or 0

    def generate():
        cursor = max(after, 0)
        while True:
            try:
                payload = wait_events(
                    task_id,
                    after=cursor,
                    timeout=STREAM_KEEPALIVE_SECONDS,
                    user_id=user_id,
                )
            except TaskAccessError:
                return
            events = payload.get("events") or []
            for event in events:
                cursor = max(cursor, int(event.get("seq") or 0))
                yield f"event: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if events:
                if payload.get("finished"):
                    # 回放完剩余事件时任务已结束：立即补发 done 关闭，不再等待。
                    body = {
                        "ok": bool(payload.get("ok")),
                        "message": payload.get("message") or "",
                    }
                    yield f"event: done\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
                    return
                continue
            if payload.get("finished"):
                # 已追平最后一帧却仍是完成态：可能只是阶段间隙（前端复用同一
                # task_id，下一阶段 POST 未到），短暂等待 reopen；没有新 POST 则
                # 补发 done 关闭连接。
                restarted = False
                deadline = time.monotonic() + PLACEHOLDER_WAIT_SECONDS
                while time.monotonic() < deadline:
                    time.sleep(0.5)
                    current = snapshot(task_id, after=cursor, user_id=user_id)
                    if current is None:
                        return
                    if current.get("events") or not current.get("finished"):
                        restarted = True
                        break
                if restarted:
                    continue
                body = {
                    "ok": bool(payload.get("ok")),
                    "message": payload.get("message") or "",
                }
                yield f"event: done\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
                return
            yield ": ping\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.route("/task/<task_id>", methods=["GET"])
def get_task_progress(task_id):
    """轮询兜底：SSE 不可用时前端按 after 拉取增量事件。"""
    if not is_valid_task_id(task_id):
        return jsonify({"success": False, "error": "task_id 非法"}), 400
    user_id = session.get("user_id")
    after = request.args.get("after", 0, type=int) or 0
    try:
        ensure_task(task_id, user_id, kind="poll")
        payload = snapshot(task_id, after=after, user_id=user_id)
    except TaskAccessError:
        return jsonify({"success": False, "error": "任务不存在"}), 403
    if payload is None:
        payload = {"events": [], "finished": False, "ok": False, "message": ""}
    return jsonify(
        {
            "success": True,
            "task_id": task_id,
            "events": payload.get("events") or [],
            "finished": bool(payload.get("finished")),
            "ok": bool(payload.get("ok")),
            "message": payload.get("message") or "",
        }
    )


@bp.route("/split", methods=["POST"])
def split_questions():
    """开始执行标准化 + OCR + 分割。

    用户点击前端"开始分割题目"后调用该接口：
    - 标准化输入（PDF/图片 → 图片列表）
    - 触发 Agent/OCR 并分割题目

    Returns:
        JSON响应，包含分割后的题目
    """
    run_id = None
    user_id = None
    task_id = None
    required_quota_amount = 1
    emitter = EventEmitter(None)
    try:
        # 读取请求体参数（模型供应商 + 模型名称）
        data = request.get_json(silent=True) or {}
        model_provider = data.get("model_provider", "openai")
        model_name = data.get("model_name")  # 可选，None 时使用 provider 默认模型
        provider_source = data.get("provider_source") or None
        provider_id = data.get("provider_id") or None
        ocr_engine = str(data.get("ocr_engine") or "paddleocr").strip().lower()
        if ocr_engine not in ("vision", "paddleocr"):
            ocr_engine = "paddleocr"
        try:
            task_id = _parse_task_id(data)
        except ValueError:
            return jsonify({"success": False, "error": "task_id 非法", "code": "INVALID_TASK_ID"}), 400
        # 从数据库加载用户的 LLM + OCR 凭据
        user_id = session.get("user_id")
        try:
            emitter = _start_progress(task_id, user_id, "split")
        except TaskAccessError:
            return jsonify({"success": False, "error": "任务不存在"}), 403
        emitter.emit(step="prepare", stage="准备", message="正在准备分割任务…")
        ocr_credentials = {}
        vision_credentials = None
        fallback_warning = ""
        should_consume_quota = False
        with SessionLocal() as db:
            try:
                llm_selection = resolve_llm_selection(
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
            if ocr_engine == "vision":
                from pipeline.vision_ocr_client import resolve_vision_credentials

                vision_credentials = resolve_vision_credentials(db, user_id)
                if not vision_credentials:
                    logger.warning("未配置支持图像理解的 Provider，分割回退 PaddleOCR")
                    ocr_engine = "paddleocr"
                    fallback_warning = "未配置支持图像理解的模型，已回退 PaddleOCR 识别"
                    emitter.emit(
                        step="prepare",
                        stage="准备",
                        level="warn",
                        message=fallback_warning,
                    )
            if user_id:
                uses_server_side_ocr = uses_server_ocr(db, user_id) or (
                    ocr_engine == "vision"
                    and vision_credentials is not None
                    and vision_credentials.get("source") == "system"
                )
                uses_server_side_llm = uses_server_llm_selection(
                    llm_selection["source"],
                    db=db,
                    user_id=user_id,
                    provider=model_provider,
                )
                should_consume_quota = uses_server_side_ocr or uses_server_side_llm
                if should_consume_quota:
                    quota_user = crud.get_user_by_id(db, user_id)
                    if not quota_user:
                        session.clear()
                        _finish_failed(emitter, "用户不存在")
                        return jsonify({"success": False, "error": "用户不存在"}), 401
                    # 点数按模型实际费用在调用结束后结算，这里只做最低 1 点的预检。
                    required_quota_amount = 1
                    if not has_daily_free_quota(db, quota_user, amount=required_quota_amount):
                        payload, status = quota_exceeded_response(
                            db,
                            quota_user,
                            amount=required_quota_amount,
                        )
                        _finish_failed(emitter, payload.get("error") or "今日免费额度已用完")
                        return jsonify(payload), status

                ocr_provider = crud.get_active_provider(db, user_id, "paddleocr")
                if ocr_provider:
                    ocr_credentials = {
                        "api_url": ocr_provider.base_url,
                        "token": ocr_provider.api_key,
                        "model": ocr_provider.model_name,
                        "use_doc_orientation": ocr_provider.use_doc_orientation,
                        "use_doc_unwarping": ocr_provider.use_doc_unwarping,
                        "use_chart_recognition": ocr_provider.use_chart_recognition,
                    }

        with session_lock:
            us = get_user_session(user_id)
            keys = ordered_file_keys(us)
            file_paths = []
            for k in keys:
                v = us["session_files"].get(k) or {}
                fp = v.get("filepath")
                if fp:
                    file_paths.append(fp)

        if not file_paths:
            _finish_failed(emitter, "请先上传文件")
            return jsonify({"success": False, "error": "请先上传文件"}), 400

        with session_lock:
            us = get_user_session(user_id)
            file_names = [
                us["session_files"].get(k, {}).get("filename", "Unknown")
                for k in ordered_file_keys(us)
            ]

        with SessionLocal() as db:
            run = run_store.create_split_run(
                db,
                user_id=user_id,
                file_names=file_names,
                model_provider=model_provider,
            )
            run_id = run.public_id
            result_dir = run.result_dir
        emitter.emit(
            step="prepare",
            stage="准备",
            level="success",
            message=f"已创建运行记录 {run_id}，共 {len(file_paths)} 个文件",
            progress=1.0,
        )

        with session_lock:
            us = get_user_session(user_id)
            us["current_thread_id"] = run_id
            thread_id = us["current_thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        initial_state = {
            "file_paths": file_paths,
            "model_provider": model_provider,
            "model_name": llm_selection["model_name"],
            "ocr_engine": ocr_engine,
            "ocr_credentials": ocr_credentials,
            "vision_credentials": vision_credentials or {},
            "ocr_cache_path": _cache_path(ocr_engine, user_id),
            "results_dir": result_dir,
            "task_id": task_id,
        }
        from core.usage import start_usage, end_usage, record_fallback_usage

        # 视觉引擎在 /api/ocr 已消费缓存时不会再次调用视觉模型，避免重复计费
        has_vision_cache = bool(
            ocr_engine == "vision"
            and user_id
            and os.path.exists(_cache_path("vision", user_id))
        )
        system_stages = set()
        if should_consume_quota and uses_server_side_llm and ocr_engine != "vision":
            system_stages.add("llm")
        if (
            ocr_engine == "vision"
            and vision_credentials
            and vision_credentials.get("source") == "system"
            and not has_vision_cache
        ):
            system_stages.add("vision_ocr")
        usage_ctx = start_usage(
            user_id,
            "split",
            run_id=run_id,
            provider_source=llm_selection["source"] if uses_server_side_llm else "",
            system_stages=system_stages,
        )
        try:
            emitter.emit(
                step="split",
                stage="分割题目",
                message="开始识别与分割题目…",
            )
            workflow_graph.invoke(initial_state, config=config)
            result_state = workflow_graph.invoke(None, config=config)
            if "llm" in system_stages and user_id:
                record_fallback_usage(
                    model_provider, llm_selection["model_name"], stage="llm"
                )
            if "vision_ocr" in system_stages and user_id:
                record_fallback_usage(
                    "openai",
                    (vision_credentials or {}).get("model") or "vision",
                    stage="vision_ocr",
                )
        finally:
            end_usage(usage_ctx)
        _mark_server_ocr_cache(user_id, False)

        questions = result_state.get("questions", [])
        warnings = list(result_state.get("warnings", []) or [])
        if fallback_warning:
            warnings.insert(0, fallback_warning)
        if questions:
            emitter.emit(
                step="split",
                stage="分割题目",
                level="success",
                message=f"分割完成：共 {len(questions)} 道题目",
                progress=1.0,
            )

        if not questions:
            failure_message = (
                warnings[0]
                if warnings
                else "未能分割出题目，请检查上传内容或模型配置"
            )
            with SessionLocal() as db:
                run_store.mark_failed(
                    db,
                    run_id,
                    user_id=user_id,
                    error_message=failure_message,
                )
            _finish_failed(emitter, failure_message)
            return (
                jsonify(
                    {
                        "success": False,
                        "code": "SPLIT_NO_QUESTIONS",
                        "error": failure_message,
                        "run_id": run_id,
                        "questions": [],
                        "warnings": warnings,
                    }
                ),
                422,
            )

        # 自动保存分割记录
        try:
            emitter.emit(step="split", stage="分割题目", message="正在保存分割结果…")
            subject = _read_split_subject(result_dir)

            with session_lock:
                us = get_user_session(user_id)
                session_files = [
                    us["session_files"].get(k, {}) for k in ordered_file_keys(us)
                ]
                file_names = [
                    item.get("filename", "Unknown") for item in session_files
                ]
                original_images = _build_original_image_payload(session_files)

            with SessionLocal() as db:
                run_store.mark_succeeded(
                    db,
                    run_id,
                    user_id=user_id,
                    subject=subject,
                    question_count=len(questions),
                )
                crud.save_split_record(
                    db,
                    subject,
                    model_provider,
                    file_names,
                    questions,
                    original_images=original_images,
                    user_id=user_id,
                )
        except Exception:
            logger.warning("保存分割记录失败，不影响主流程", exc_info=True)

        _finish_ok(emitter, f"成功分割 {len(questions)} 道题目")
        return jsonify(
            {
                "success": True,
                "engine": ocr_engine,
                "message": f"成功分割 {len(questions)} 道题目",
                "run_id": run_id,
                "questions": questions,
                "warnings": warnings,
            }
        )

    except Exception as e:
        logger.exception("题目分割失败")
        if run_id:
            try:
                with SessionLocal() as db:
                    run_store.mark_failed(
                        db, run_id, user_id=user_id, error_message=str(e)
                    )
            except Exception:
                logger.warning("failed to mark workflow run as failed", exc_info=True)
        _finish_failed(emitter, "题目分割失败，请稍后重试")
        return jsonify({"success": False, "error": "题目分割失败，请稍后重试"}), 500


@bp.route("/split-records", methods=["GET"])
def get_split_records():
    """获取最近 N 次分割历史记录，limit 由前端通过查询参数指定"""
    try:
        limit = request.args.get("limit", 10, type=int)
        limit = max(1, min(limit, crud.MAX_SPLIT_RECORDS))  # 上限与保留条数一致

        with SessionLocal() as db:
            records = crud.get_recent_split_records(
                db, limit, user_id=session.get("user_id")
            )
            result = [_serialize_split_record(r) for r in records]

        return jsonify({"success": True, "records": result})

    except Exception as e:
        logger.exception("获取分割记录失败")
        return jsonify({"success": False, "error": "获取分割记录失败"}), 500


@bp.route("/split-records/<int:record_id>", methods=["GET"])
def get_split_record_detail(record_id):
    """获取单条分割记录的完整数据（含 questions）"""
    try:
        with SessionLocal() as db:
            uid = None if session.get("is_admin") else session.get("user_id")
            record = crud.get_split_record_by_id(db, record_id, user_id=uid)
            if not record:
                return jsonify({"success": False, "error": "记录不存在"}), 404

            result = _serialize_split_record(record)
            result["questions"] = (
                json.loads(record.questions_json) if record.questions_json else []
            )

        return jsonify({"success": True, "record": result})

    except Exception as e:
        logger.exception("获取分割记录详情失败")
        return jsonify({"success": False, "error": "Get split record detail failed"}), 500


@bp.route("/export", methods=["POST"])
def export_wrongbook():
    """
    注入选中题目 ID 并恢复图执行导出

    图执行: export → END

    Returns:
        JSON响应，包含导出文件路径
    """
    try:
        data = request.get_json(silent=True) or {}
        selected_uids = data.get("selected_ids", [])

        if not isinstance(selected_uids, list):
            return jsonify({"success": False, "error": "selected_ids 必须为列表"}), 400

        if not selected_uids:
            return jsonify({"success": False, "error": "未选择任何题目"}), 400

        # 检查是否已分割（通过 questions.json 存在性判断，不依赖内存中的 current_thread_id）
        run_id = data.get("run_id")
        if not run_id:
            return jsonify({"success": False, "code": "MISSING_RUN_ID", "error": "缺少 run_id，请重新分割题目"}), 400
        user_id = session.get("user_id")
        with SessionLocal() as db:
            run = run_store.get_split_run(db, run_id, user_id=user_id)
            if not run or run.status != run_store.STATUS_SUCCEEDED:
                return jsonify({"success": False, "error": "请先分割题目"}), 400
            questions = run_store.read_questions(run)
        if not questions:
            return jsonify({"success": False, "error": "请先分割题目"}), 400

        output_path = export_wrongbook_md(
            questions,
            selected_uids,
            output_path=str(run_store.wrongbook_path(run)),
        )
        download_url = f"/download/{os.path.basename(output_path)}"

        return jsonify(
            {
                "success": True,
                "message": "错题本导出成功",
                "output_path": output_path,
                "download_url": download_url,
                "run_id": run.public_id,
            }
        )

    except Exception as e:
        logger.exception("导出失败")
        return jsonify({"success": False, "error": "导出失败，请稍后重试"}), 500


@bp.route("/image/<filename>")
def serve_image(filename):
    """提供上传/处理后的图片文件访问（OCR 预览用）"""
    from flask import send_from_directory

    # 在多个目录中查找
    for d in [
        settings.upload_dir,
        settings.erased_dir,
        settings.results_dir,
        settings.pages_dir,
        settings.struct_dir,
    ]:
        path = os.path.join(str(d), filename)
        if os.path.exists(path):
            return send_from_directory(str(d), filename)
    return jsonify({"error": "File not found"}), 404


@bp.route("/question-image/<path:filename>")
def serve_question_image(filename):
    """提供题目配图（图形裁剪 / 整页原图）访问。

    send_from_directory 已拒绝 .. 越界；这里再显式拒绝绝对路径与盘符。
    """
    from flask import send_from_directory

    candidate = filename or ""
    if (
        not candidate
        or os.path.splitdrive(candidate)[0]
        or os.path.isabs(candidate)
        or candidate.startswith(("/", "\\"))
        or any(part == ".." for part in candidate.replace("\\", "/").split("/"))
    ):
        return jsonify({"error": "File not found"}), 404

    root = str(settings.question_images_dir)
    os.makedirs(root, exist_ok=True)
    return send_from_directory(root, candidate)
