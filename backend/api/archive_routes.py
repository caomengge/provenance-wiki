"""
archive_routes.py – Choosing, opening and moving the archive (desktop app).

Spec: specs/archive-location.md. Logic: modules/archive_location.py.

Routes:
  GET  /api/archive/location            state, path, size, free space, old copy, move status
  POST /api/archive/choose-folder       show the system folder picker on this computer
  POST /api/archive/choose-folder/cancel close a folder dialog that is open or stuck
  POST /api/archive/check               apply the destination rules to a folder
  POST /api/archive/setup               first launch / start a new archive at a folder
  POST /api/archive/locate              open an existing archive (marker must match unless allowed)
  POST /api/archive/retry               look for a missing archive again (drive reconnected)
  POST /api/archive/move                start moving the archive
  GET  /api/archive/move/status         progress of the current/last move
  POST /api/archive/move/cancel         cancel a move (nothing changes)
  POST /api/archive/old-copy/delete     delete the old copy after a move
  POST /api/archive/old-copy/forget     keep the old copy and stop reminding

All writes go through modules/local_security.py (same origin + header).
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

bp = Blueprint("archive", __name__)
logger = logging.getLogger(__name__)


def _al():
    from modules import archive_location
    return archive_location


def _location_payload(include_size: bool = True) -> dict:
    import config
    al = _al()
    state = config.ARCHIVE_STATE
    payload = {
        "state": state,
        "packaged": config.PACKAGED,
        "default_path": str(al.default_archive_dir()),
        "default_free_bytes": al.free_space(al.default_archive_dir()),
        "default_drive": al.drive_label(al.default_archive_dir()),
        "expected_path": config.ARCHIVE_EXPECTED,
        "path": None,
        "move": al.move_status(),
    }
    if state in ("ok", "moving"):
        path = Path(config.DATA_DIR)
        marker = al.read_marker(path) or {}
        payload.update({
            "path": str(path),
            "archive_id": marker.get("archive_id"),
            "is_default": str(path) == str(al.default_archive_dir()),
            "drive": al.drive_label(path),
            "free_bytes": al.free_space(path),
            "removable": al.is_removable_path(path),
        })
        if include_size:
            payload["size_bytes"] = al.folder_size(path)
        payload["previous_copy"] = al.previous_copy() if state == "ok" else None
    return payload


def _error(message: str, status: int = 400, **extra):
    return jsonify({"error": message, **extra}), status


@bp.route("/api/archive/location", methods=["GET"])
def location():
    return jsonify(_location_payload(include_size=request.args.get("size", "1") != "0"))


@bp.route("/api/archive/choose-folder", methods=["POST"])
def choose_folder():
    al = _al()
    prompt = "Choose where to store your Provenance Wiki archive"
    try:
        path = al.choose_folder(prompt)
    except al.PickerUnavailable:
        return _error("The folder picker isn't available. Type the folder path instead.", 501)
    except Exception:
        logger.exception("Folder picker failed")
        return _error("The folder picker didn't open. Type the folder path instead.", 501)
    return jsonify({"path": path, "cancelled": path is None})


@bp.route("/api/archive/choose-folder/cancel", methods=["POST"])
def cancel_choose_folder():
    return jsonify({"closed": _al().cancel_folder_picker()})


@bp.route("/api/archive/check", methods=["POST"])
def check():
    import config
    al = _al()
    data = request.get_json(silent=True) or {}
    purpose = data.get("purpose") if data.get("purpose") in ("setup", "move") else "setup"
    current, size = None, 0
    if purpose == "move" and config.ARCHIVE_STATE == "ok":
        current = Path(config.DATA_DIR)
        size = al.folder_size(current)
    return jsonify(al.check_destination(data.get("path", ""), purpose,
                                        current=current, archive_bytes=size))


@bp.route("/api/archive/setup", methods=["POST"])
def setup():
    import config
    al = _al()
    data = request.get_json(silent=True) or {}
    if config.ARCHIVE_STATE == "missing" and not data.get("confirm_new"):
        return _error("Confirm that you want to start a new, empty archive.", 409)
    if config.ARCHIVE_STATE not in ("setup", "missing"):
        return _error("An archive is already open. Use Change location to move it.", 409)
    path = str(al.default_archive_dir()) if data.get("use_default") else data.get("path", "")
    try:
        result = al.setup_archive(path)
    except al.ArchiveError as exc:
        return _error(str(exc))
    return jsonify({**result, "location": _location_payload(include_size=False)})


@bp.route("/api/archive/locate", methods=["POST"])
def locate():
    import config
    al = _al()
    data = request.get_json(silent=True) or {}
    if config.ARCHIVE_STATE == "moving":
        return _error("Wait until the move has finished.", 409)
    try:
        result = al.locate_archive(data.get("path", ""), bool(data.get("allow_different")))
    except al.ArchiveError as exc:
        if str(exc) == "DIFFERENT_ARCHIVE":
            return _error("This folder contains a different Provenance Wiki archive from the one "
                          "this computer was using.", 409, code="different_archive")
        return _error(str(exc))
    return jsonify({**result, "location": _location_payload(include_size=False)})


@bp.route("/api/archive/retry", methods=["POST"])
def retry():
    import config
    if config.ARCHIVE_STATE not in ("missing", "setup"):
        return jsonify(_location_payload(include_size=False))
    _al().retry()
    return jsonify(_location_payload(include_size=False))


@bp.route("/api/archive/move", methods=["POST"])
def move():
    al = _al()
    data = request.get_json(silent=True) or {}
    try:
        status = al.start_move(data.get("path", ""))
    except al.ArchiveError as exc:
        return _error(str(exc))
    return jsonify(status), 202


@bp.route("/api/archive/move/status", methods=["GET"])
def move_status():
    return jsonify(_al().move_status() or {})


@bp.route("/api/archive/move/cancel", methods=["POST"])
def move_cancel():
    _al().cancel_move()
    return jsonify({"cancelling": True})


@bp.route("/api/archive/old-copy/delete", methods=["POST"])
def delete_old_copy():
    import config
    al = _al()
    if not (request.get_json(silent=True) or {}).get("confirm"):
        return _error("Confirm the deletion.", 409)
    if config.ARCHIVE_STATE != "ok":
        return _error("The archive isn't available right now.", 409)
    try:
        al.delete_previous_copy()
    except al.ArchiveError as exc:
        return _error(str(exc))
    except OSError:
        logger.exception("Deleting the old copy failed")
        return _error("Some files of the old copy couldn't be deleted. You can delete the folder "
                      "yourself.", 500)
    return jsonify(_location_payload(include_size=False))


@bp.route("/api/archive/old-copy/forget", methods=["POST"])
def forget_old_copy():
    _al().forget_previous_copy()
    return jsonify(_location_payload(include_size=False))
