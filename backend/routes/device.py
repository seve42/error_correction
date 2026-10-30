import logging
import os
import uuid
from datetime import timezone
from pathlib import PurePath
from typing import Optional

from flask import Blueprint, jsonify, request, session

from core.config import settings
from core.state import get_user_session, normalize_device_id, session_lock
from db import SessionLocal, crud

logger = logging.getLogger(__name__)

bp = Blueprint("device", __name__)


def _allowed_image_file(filename: str) -> bool:
    return PurePath(filename).suffix.lower().lstrip(".") in settings.allowed_extensions


def _isoformat_utc(dt) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _ocr_cache_path(user_id: Optional[int]) -> str:
    key = str(user_id) if user_id is not None else "anon"
    return os.path.join(str(settings.results_dir), f"ocr_cache_{key}.json")


def _clear_ocr_cache(user_id: Optional[int]) -> None:
    import glob

    key = str(user_id) if user_id is not None else "anon"
    pattern = os.path.join(str(settings.results_dir), f"ocr_cache_{key}*.json")
    for cache_path in glob.glob(pattern):
        try:
            os.remove(cache_path)
        except FileNotFoundError:
            pass


def _clear_workbench_references(user_id: Optional[int]) -> None:
    with session_lock:
        us = get_user_session(user_id)
        us["current_thread_id"] = None
        us["session_files"].clear()
        us["session_file_order"].clear()
        us["cancelled_file_keys"].clear()
        us["erased_file_paths"] = None
        us["ocr_cache_uses_server_ocr"] = False
    _clear_ocr_cache(user_id)


def _store_file_in_workbench_session(user_id: Optional[int], file, filename: str) -> dict:
    file_key = uuid.uuid4().hex
    original_ext = os.path.splitext(filename)[1].lower().lstrip(".") or "jpg"
    stored_name = f"{uuid.uuid4().hex}.{original_ext}"
    filepath = os.path.join(str(settings.upload_dir), stored_name)
    file.save(filepath)

    with session_lock:
        us = get_user_session(user_id)
        if us["current_thread_id"] is not None:
            us["current_thread_id"] = None
            us["session_files"].clear()
            us["session_file_order"].clear()
            us["ocr_cache_uses_server_ocr"] = False
            _clear_ocr_cache(user_id)

        us["session_files"][file_key] = {
            "filename": filename,
            "filepath": filepath,
        }
        us["session_file_order"].append(file_key)
        us["ocr_cache_uses_server_ocr"] = False
        _clear_ocr_cache(user_id)

    return {
        "file_key": file_key,
        "filename": filename,
        "filepath": filepath,
    }


def _add_existing_file_to_workbench_session(
    user_id: Optional[int], *, filepath: str, filename: str
) -> dict:
    file_key = uuid.uuid4().hex
    with session_lock:
        us = get_user_session(user_id)
        if us["current_thread_id"] is not None:
            us["current_thread_id"] = None
            us["session_files"].clear()
            us["session_file_order"].clear()
            us["ocr_cache_uses_server_ocr"] = False
            _clear_ocr_cache(user_id)

        us["session_files"][file_key] = {
            "filename": filename,
            "filepath": filepath,
        }
        us["session_file_order"].append(file_key)
        us["ocr_cache_uses_server_ocr"] = False
        _clear_ocr_cache(user_id)

    return {
        "file_key": file_key,
        "filename": filename,
        "filepath": filepath,
    }


def _device_qr_payload(device_uuid: str) -> str:
    return f"aiwb://bind?device_uuid={device_uuid}"


def _serialize_device_capture(capture) -> dict:
    return {
        "id": capture.id,
        "device_uuid": capture.device_uuid,
        "file_key": capture.file_key,
        "filename": capture.original_filename,
        "image_url": f"/api/image/{os.path.basename(capture.file_path)}",
        "content_type": capture.content_type,
        "file_size": capture.file_size,
        "created_at": _isoformat_utc(capture.created_at),
    }


@bp.route("/bind", methods=["POST"])
def bind_device():
    """Create or return the current user's hardware camera UUID."""
    data = request.get_json(silent=True) or {}
    force_new = bool(data.get("force_new"))

    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"success": False, "error": "unauthorized"}), 401

    with SessionLocal() as db:
        binding = None if force_new else crud.get_latest_user_device_binding(db, user_id)
        if binding is None:
            binding = crud.create_device_binding(db, user_id)

    return jsonify(
        {
            "success": True,
            "device_uuid": binding.device_uuid,
            "qr_payload": _device_qr_payload(binding.device_uuid),
        }
    )


@bp.route("/binding", methods=["GET"])
def get_device_binding():
    """Return the current user's active hardware camera binding, if any."""
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"success": False, "error": "unauthorized"}), 401

    with SessionLocal() as db:
        binding = crud.get_latest_user_device_binding(db, user_id)

    if binding is None:
        return jsonify(
            {
                "success": True,
                "bound": False,
                "device_uuid": None,
                "qr_payload": None,
            }
        )

    return jsonify(
        {
            "success": True,
            "bound": True,
            "device_uuid": binding.device_uuid,
            "qr_payload": _device_qr_payload(binding.device_uuid),
        }
    )


@bp.route("/unbind", methods=["POST"])
def unbind_device():
    data = request.get_json(silent=True) or {}
    device_uuid = normalize_device_id(
        data.get("device_uuid") or data.get("device_id") or request.form.get("device_uuid")
    )
    if not device_uuid:
        return jsonify({"success": False, "error": "missing device_uuid"}), 400

    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"success": False, "error": "unauthorized"}), 401

    with SessionLocal() as db:
        ok = crud.deactivate_user_device_binding(db, user_id, device_uuid)
    if not ok:
        return jsonify({"success": False, "error": "device not found"}), 404
    return jsonify({"success": True, "device_uuid": device_uuid})


@bp.route("/images", methods=["GET"])
def list_device_images():
    user_id = session.get("user_id")
    device_uuid = normalize_device_id(
        request.args.get("device_uuid") or request.args.get("device_id")
    )
    limit = request.args.get("limit", 50, type=int)

    with SessionLocal() as db:
        if device_uuid and not crud.get_user_device_binding(db, user_id, device_uuid):
            return jsonify({"success": False, "error": "device not found"}), 404
        captures = crud.get_device_captures(
            db, user_id=user_id, device_uuid=device_uuid or None, limit=limit
        )
        items = [_serialize_device_capture(c) for c in captures]

    return jsonify({"success": True, "images": items})


@bp.route("/images/use", methods=["POST"])
def use_device_images():
    """Add selected device captures to the current AI workbench upload queue."""
    data = request.get_json(silent=True) or {}
    capture_ids = data.get("capture_ids") or []
    reset_session = bool(data.get("reset_session", True))
    if not isinstance(capture_ids, list) or not capture_ids:
        return jsonify({"success": False, "error": "missing capture_ids"}), 400

    try:
        capture_ids = [int(x) for x in capture_ids]
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "capture_ids must be integers"}), 400

    user_id = session.get("user_id")
    with SessionLocal() as db:
        captures = crud.get_device_captures_by_ids(db, user_id=user_id, ids=capture_ids)

    found_ids = {c.id for c in captures}
    missing_ids = [x for x in capture_ids if x not in found_ids]
    if missing_ids:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "some captures were not found",
                    "missing_ids": missing_ids,
                }
            ),
            404,
        )

    if reset_session:
        _clear_workbench_references(user_id)

    files = []
    for capture in captures:
        if not os.path.exists(capture.file_path):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "capture file is missing",
                        "capture_id": capture.id,
                    }
                ),
                410,
            )
        added = _add_existing_file_to_workbench_session(
            user_id,
            filepath=capture.file_path,
            filename=capture.original_filename,
        )
        files.append(
            {
                "capture_id": capture.id,
                "file_key": added["file_key"],
                "filename": added["filename"],
            }
        )

    return jsonify(
        {
            "success": True,
            "message": "device captures added to workbench",
            "result": {
                "file_count": len(files),
                "files": files,
            },
        }
    )


@bp.route("/capture", methods=["POST"])
def upload_device_capture():
    """Receive a camera button capture from firmware."""
    device_uuid = normalize_device_id(
        request.form.get("device_uuid") or request.form.get("device_id")
    )
    if not device_uuid:
        return jsonify({"success": False, "error": "missing device_uuid"}), 400
    if len(device_uuid) > 128:
        return jsonify({"success": False, "error": "device_uuid is too long"}), 400

    with SessionLocal() as db:
        binding = crud.get_active_device_binding(db, device_uuid)
        if binding is None:
            return jsonify({"success": False, "error": "device is not bound"}), 404
        user_id = binding.user_id

    image = request.files.get("image")
    if image is None or image.filename == "":
        return jsonify({"success": False, "error": "missing image"}), 400

    filename = image.filename or "capture.jpg"
    content_type = (image.content_type or "").lower()
    if content_type and content_type not in {"image/jpeg", "image/jpg"}:
        return jsonify({"success": False, "error": "image must be JPEG"}), 400
    if not _allowed_image_file(filename):
        return jsonify({"success": False, "error": "unsupported image filename"}), 400

    image.seek(0, 2)
    file_size = image.tell()
    image.seek(0)
    if file_size == 0:
        return jsonify({"success": False, "error": "empty image"}), 400
    if file_size > settings.max_file_size_mb * 1024 * 1024:
        return jsonify({"success": False, "error": "image is too large"}), 413

    try:
        os.makedirs(str(settings.upload_dir), exist_ok=True)
        stored = _store_file_in_workbench_session(user_id, image, filename)
        with SessionLocal() as db:
            capture = crud.create_device_capture(
                db,
                device_uuid=device_uuid,
                user_id=user_id,
                file_key=stored["file_key"],
                original_filename=filename,
                file_path=stored["filepath"],
                content_type=content_type,
                file_size=file_size,
            )
        return jsonify(
            {
                "success": True,
                "message": "capture uploaded",
                "device_uuid": device_uuid,
                "capture_id": capture.id,
                "file_key": stored["file_key"],
                "filename": stored["filename"],
            }
        ), 201
    except Exception:
        logger.exception("device capture upload failed")
        return jsonify({"success": False, "error": "upload failed"}), 500
