"""
笔记模块 API 路由

流程：上传笔记图片 → PaddleOCR 识别 → LLM 整理 → 保存到数据库
"""

import os
import uuid
import json
import logging
from pathlib import PurePath

from flask import Blueprint, request, jsonify, session

from core.config import settings
from core.quota import (
    has_daily_free_quota,
    quota_exceeded_response,
    uses_server_llm,
    uses_server_ocr,
    uses_server_llm_selection,
)
from core.model_selection import LLMSelectionError, resolve_llm_selection
from db import SessionLocal
from db import crud

logger = logging.getLogger(__name__)

bp = Blueprint("notes", __name__)


def _effective_user_id():
    """管理员返回 None（不过滤），普通用户返回 user_id"""
    if session.get("is_admin"):
        return None
    return session.get("user_id")


def _project_id_arg():
    value = request.args.get("project_id") or None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _project_id_form():
    value = request.form.get("project_id") or None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _allowed_image(filename):
    """检查是否为允许的图片格式"""
    return PurePath(filename).suffix.lower().lstrip(".") in {
        "png",
        "jpg",
        "jpeg",
        "bmp",
        "tiff",
        "webp",
    }


def _build_note_preview_payload(result, source_images: list[str], ocr_text: str) -> dict:
    """构造未保存的笔记整理结果，供前端确认后再保存。"""
    return {
        "title": result.title,
        "subject": result.subject,
        "content_markdown": result.content_markdown,
        "knowledge_tags": list(result.knowledge_tags or []),
        "source_images": list(source_images or []),
        "ocr_text": ocr_text,
    }


def _serialize_note(note) -> dict:
    """将 Note ORM 对象序列化为前端 JSON"""
    knowledge_tags = []
    if note.tags:
        for mapping in note.tags:
            if mapping.tag:
                knowledge_tags.append(mapping.tag.tag_name)

    payload = {
        "id": note.id,
        "title": note.title,
        "subject": note.subject,
        "content_markdown": note.content_markdown,
        "source_images": (
            json.loads(note.source_images_json) if note.source_images_json else []
        ),
        "knowledge_tags": knowledge_tags,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
    payload.update(crud.serialize_review_fields(note))
    return payload


def _apply_provider_context(db, user_id):
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


@bp.route("/", methods=["POST"])
def create_note():
    """上传笔记图片 → OCR → LLM 整理；若提供 project_id 则直接保存。

    请求：multipart/form-data
      - files: 一张或多张图片
      - model_provider: 模型供应商（可选，默认 openai）
      - model_name: 模型名称（可选）
    """
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"success": False, "error": "请上传笔记图片"}), 400

    # 校验文件格式
    for f in files:
        if f.filename and not _allowed_image(f.filename):
            return (
                jsonify({"success": False, "error": f"不支持的图片格式: {f.filename}"}),
                400,
            )

    user_id = session.get("user_id")
    raw_project_id = request.form.get("project_id") or None
    if raw_project_id is None:
        project_id = None
    else:
        try:
            project_id = int(raw_project_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "请选择一个笔记本"}), 400
    model_provider = request.form.get("model_provider", "openai")
    model_name = request.form.get("model_name") or None
    provider_source = request.form.get("provider_source") or None
    provider_id = request.form.get("provider_id") or None

    try:
        should_consume_quota = False
        uses_server_side_llm = False
        with SessionLocal() as db:
            if project_id is not None:
                try:
                    project_id = crud.require_project_id(
                        db, project_id, user_id=user_id, project_type="note"
                    )
                except ValueError as exc:
                    if str(exc) == "PROJECT_TYPE_MISMATCH":
                        return jsonify({"success": False, "error": "请选择一个笔记本"}), 400
                    return jsonify({"success": False, "error": "项目不存在"}), 404

            # 1. 解析模型选择
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
                return (
                    jsonify({"success": False, "code": e.code, "error": e.message}),
                    e.status_code,
                )

            if user_id:
                uses_server_side_ocr = uses_server_ocr(db, user_id)
                # 根据解析出的 source 决定是否扣减额度
                uses_server_side_llm = uses_server_llm_selection(
                    llm_selection["source"]
                )
                should_consume_quota = uses_server_side_ocr or uses_server_side_llm
                if should_consume_quota:
                    quota_user = crud.get_user_by_id(db, user_id)
                    if not quota_user:
                        session.clear()
                        return jsonify({"success": False, "error": "用户不存在"}), 401
                    if not has_daily_free_quota(db, quota_user):
                        payload, status = quota_exceeded_response(db, quota_user)
                        return jsonify(payload), status

        # 1. 保存上传图片
        saved_paths = []
        for f in files:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(str(settings.upload_dir), filename)
            f.save(filepath)
            saved_paths.append(filepath)

        if not saved_paths:
            return jsonify({"success": False, "error": "没有有效的图片文件"}), 400

        # 2. OCR 识别
        from pipeline.paddleocr_client import PaddleOCRClient
        from pipeline.utils import simplify_ocr_results, run_async

        # 从数据库加载用户 OCR 凭据
        ocr_kwargs = {}
        with SessionLocal() as db:
            _apply_provider_context(db, user_id)
            if user_id:
                ocr_provider = crud.get_active_provider(db, user_id, "paddleocr")
                if ocr_provider:
                    ocr_kwargs = {
                        "api_url": ocr_provider.base_url,
                        "token": ocr_provider.api_key,
                        "model": ocr_provider.model_name,
                    }

        client = PaddleOCRClient(**ocr_kwargs)
        raw_results = run_async(client.parse_images_async(saved_paths))
        simplified = simplify_ocr_results(raw_results)

        # 拼接 OCR 文本 + 图片引用
        ocr_text_parts = []
        image_refs = []
        for page in simplified:
            for block in page.get("blocks", []):
                label = block.get("block_label", "")
                content = block.get("block_content", "").strip()
                if label in ("image", "chart") and content:
                    # 图片块：记录路径，在文本中插入占位符
                    image_refs.append(content)
                    ocr_text_parts.append(f"[图片: {content}]")
                elif content:
                    ocr_text_parts.append(content)
        ocr_text = "\n".join(ocr_text_parts)

        if not ocr_text.strip():
            return (
                jsonify(
                    {"success": False, "error": "OCR 未识别到任何文字，请检查图片"}
                ),
                400,
            )

        # 3. LLM 整理
        from agents.note import invoke_note_organize
        from core.usage import start_usage, end_usage

        usage_ctx = start_usage(
            user_id,
            "note",
            provider_source=llm_selection["source"],
            system_stages={"llm"} if uses_server_side_llm else set(),
        )
        # 使用解析出的 credentials 调用代理
        try:
            result = invoke_note_organize(
                ocr_text,
                provider=llm_selection["category"],
                model_name=llm_selection["model_name"],
                api_key=llm_selection["api_key"],
                base_url=llm_selection["base_url"],
                supports_function_calling=llm_selection["supports_function_calling"],
            )
        except Exception as e:
            msg = str(e)
            if "暂不支持该模型" in msg or "errorCode" in msg and "40405" in msg:
                return (
                    jsonify(
                        {
                            "success": False,
                            "code": "MODEL_NOT_SUPPORTED",
                            "error": (
                                "所选模型在当前 API 提供方不受支持。请检查该配置的 Base URL 与模型名称是否匹配，"
                                "或在设置中更换为可用模型后重试。"
                            ),
                        }
                    ),
                    400,
                )
            if "Error code: 400" in msg or "BadRequest" in msg:
                return (
                    jsonify(
                        {
                            "success": False,
                            "code": "MODEL_REQUEST_FAILED",
                            "error": "笔记整理失败：当前模型拒绝了结构化整理请求，请检查模型名称或更换支持 JSON 输出的模型。",
                        }
                    ),
                    400,
                )
            raise
        finally:
            end_usage(usage_ctx)

        note_preview = _build_note_preview_payload(result, saved_paths, ocr_text)

        # 4. 未选择笔记本时，仅返回整理结果供前端确认保存
        if project_id is None:
            return jsonify({"success": True, "note_preview": note_preview}), 200

        # 5. 保存到数据库
        with SessionLocal() as db:
            try:
                project_id = crud.require_project_id(
                    db, project_id, user_id=user_id, project_type="note"
                )
            except ValueError as exc:
                if str(exc) == "PROJECT_TYPE_MISMATCH":
                    return jsonify({"success": False, "error": "请选择一个笔记本"}), 400
                return jsonify({"success": False, "error": "项目不存在"}), 404
            note = crud.save_note(
                db,
                title=note_preview["title"],
                subject=note_preview["subject"],
                content_markdown=note_preview["content_markdown"],
                source_images=note_preview["source_images"],
                ocr_text=note_preview["ocr_text"],
                knowledge_tags=note_preview["knowledge_tags"],
                user_id=user_id,
                project_id=project_id,
            )
            note_id = note.id

        with SessionLocal() as db:
            note = crud.get_note_by_id(db, note_id, user_id=_effective_user_id())
            return (
                jsonify(
                    {
                        "success": True,
                        "note": _serialize_note(note),
                    }
                ),
                201,
            )

    except Exception as e:
        logger.exception("笔记创建失败")
        return jsonify({"success": False, "error": f"笔记创建失败: {str(e)}"}), 500


@bp.route("/save-organized", methods=["POST"])
def save_organized_note():
    """保存前端确认后的笔记整理结果到指定笔记本。"""
    try:
        data = request.get_json(silent=True) or {}
        user_id = session.get("user_id")
        project_id = data.get("project_id")
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "请先选择一个笔记本"}), 400

        with SessionLocal() as db:
            try:
                project_id = crud.require_project_id(
                    db, project_id, user_id=user_id, project_type="note"
                )
            except ValueError as exc:
                if str(exc) == "PROJECT_TYPE_MISMATCH":
                    return jsonify({"success": False, "error": "请选择一个笔记本"}), 400
                return jsonify({"success": False, "error": "项目不存在"}), 404

            note = crud.save_note(
                db,
                title=(data.get("title") or "").strip() or "未命名笔记",
                subject=(data.get("subject") or "").strip() or "未知",
                content_markdown=data.get("content_markdown") or "",
                source_images=[
                    str(item)
                    for item in (data.get("source_images") or [])
                    if str(item).strip()
                ],
                ocr_text=data.get("ocr_text") or "",
                knowledge_tags=[
                    str(item).strip()
                    for item in (data.get("knowledge_tags") or [])
                    if str(item).strip()
                ],
                user_id=user_id,
                project_id=project_id,
            )

            return jsonify({"success": True, "note": _serialize_note(note)}), 201
    except Exception:
        logger.exception("保存整理后的笔记失败")
        return jsonify({"success": False, "error": "保存笔记失败"}), 500


@bp.route("/", methods=["GET"])
def list_notes():
    """分页查询笔记列表

    查询参数：page, limit, subject, knowledge_tag, keyword
    """
    try:
        page = max(1, request.args.get("page", 1, type=int))
        limit = min(100, max(1, request.args.get("limit", 20, type=int)))
        subject = request.args.get("subject") or None
        knowledge_tag = request.args.get("knowledge_tag") or None
        keyword = request.args.get("keyword") or None
        user_id = session.get("user_id")
        project_id = _project_id_arg()

        with SessionLocal() as db:
            notes, total = crud.get_notes(
                db,
                user_id=user_id,
                subject=subject,
                knowledge_tag=knowledge_tag,
                keyword=keyword,
                page=page,
                page_size=limit,
                project_id=project_id,
            )
            return jsonify(
                {
                    "success": True,
                    "items": [_serialize_note(n) for n in notes],
                    "total": total,
                    "page": page,
                    "total_pages": (total + limit - 1) // limit,
                }
            )

    except Exception as e:
        logger.exception("获取笔记列表失败")
        return jsonify({"success": False, "error": "获取笔记列表失败"}), 500


@bp.route("/<int:note_id>", methods=["GET"])
def get_note(note_id):
    """获取单条笔记详情"""
    try:
        with SessionLocal() as db:
            note = crud.get_note_by_id(db, note_id, user_id=_effective_user_id())
            if not note:
                return jsonify({"success": False, "error": "笔记不存在"}), 404
            return jsonify(
                {
                    "success": True,
                    "note": _serialize_note(note),
                }
            )
    except Exception as e:
        logger.exception("获取笔记详情失败")
        return jsonify({"success": False, "error": "获取笔记详情失败"}), 500


@bp.route("/<int:note_id>", methods=["PATCH"])
def update_note_route(note_id):
    """更新笔记（标题、内容、科目、标签）"""
    try:
        data = request.get_json(silent=True) or {}
        with SessionLocal() as db:
            note = crud.update_note(
                db,
                note_id=note_id,
                title=data.get("title"),
                content_markdown=data.get("content_markdown"),
                subject=data.get("subject"),
                knowledge_tags=data.get("knowledge_tags"),
                user_id=_effective_user_id(),
            )
            if not note:
                return jsonify({"success": False, "error": "笔记不存在"}), 404
            return jsonify(
                {
                    "success": True,
                    "note": _serialize_note(note),
                }
            )
    except Exception as e:
        logger.exception("更新笔记失败")
        return jsonify({"success": False, "error": "更新笔记失败"}), 500


@bp.route("/<int:note_id>/review", methods=["POST"])
def review_note(note_id):
    """Record a spaced-repetition review result for a note."""
    try:
        data = request.get_json(silent=True) or {}
        with SessionLocal() as db:
            note = crud.schedule_note_review(
                db,
                note_id,
                rating=data.get("rating") or data.get("quality") or "good",
                user_id=_effective_user_id(),
            )
            if not note:
                return jsonify({"success": False, "error": "笔记不存在"}), 404
            return jsonify({"success": True, "note": _serialize_note(note)})
    except ValueError:
        return jsonify({"success": False, "error": "无效的复习结果"}), 400
    except Exception as e:
        logger.exception("记录笔记复习失败")
        return jsonify({"success": False, "error": "记录复习失败"}), 500


@bp.route("/<int:note_id>", methods=["DELETE"])
def delete_note_route(note_id):
    """删除笔记"""
    try:
        with SessionLocal() as db:
            if not crud.delete_note(db, note_id, user_id=_effective_user_id()):
                return jsonify({"success": False, "error": "笔记不存在"}), 404
            return jsonify({"success": True})
    except Exception as e:
        logger.exception("删除笔记失败")
        return jsonify({"success": False, "error": "删除笔记失败"}), 500


@bp.route("/subjects", methods=["GET"])
def list_note_subjects():
    """获取笔记库中的科目列表"""
    try:
        with SessionLocal() as db:
            subjects = crud.get_note_subjects(db, user_id=_effective_user_id(), project_id=_project_id_arg())
            return jsonify({"success": True, "subjects": subjects})
    except Exception as e:
        logger.exception("获取笔记科目列表失败")
        return jsonify({"success": False, "error": "获取科目列表失败"}), 500


@bp.route("/tags", methods=["GET"])
def list_note_tags():
    """获取笔记库中的知识点标签列表"""
    try:
        subject = request.args.get("subject") or None
        with SessionLocal() as db:
            tags = crud.get_note_tag_names(
                db, subject=subject, user_id=_effective_user_id(), project_id=_project_id_arg()
            )
            return jsonify({"success": True, "tags": tags})
    except Exception as e:
        logger.exception("获取笔记标签列表失败")
        return jsonify({"success": False, "error": "获取标签列表失败"}), 500
