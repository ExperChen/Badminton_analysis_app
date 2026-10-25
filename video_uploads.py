"""Durable, account-scoped resumable uploads for the single-process service."""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from fastapi import HTTPException

import accounts
import video_tools as videos
import drive_storage
import upload_limits

CHUNK_SIZE = 8 * 1024 * 1024
MAX_SIZE = 50 * 1024**3
TTL = 24 * 3600
MAX_PENDING = 3
LOCK = threading.RLock()
UPLOAD_LOCKS = {}
FINALIZERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drive-video-validation")
FINISHING = {}


def storage_mode():
    mode = os.environ.get("YUXU_UPLOAD_STORAGE", "google_drive")
    if mode not in {"local", "google_drive"}:
        raise HTTPException(503, "视频存储配置无效")
    return mode


def owner_id():
    user = accounts.current()
    if accounts.ENFORCE_AUTH and not user:
        raise HTTPException(401, "请先登录")
    return user["id"] if user else "local"


def paths(upload_id):
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        raise HTTPException(404, "上传不存在")
    root = videos.MEDIA_ROOT.resolve()
    return root / "uploads" / (upload_id + ".json"), root / "originals" / (upload_id + ".upload")


def upload_lock(upload_id):
    paths(upload_id)
    with LOCK:
        return UPLOAD_LOCKS.setdefault(upload_id, threading.RLock())


def load(upload_id):
    manifest, _ = paths(upload_id)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, "上传不存在") from None
    if data["owner_id"] != owner_id():
        raise HTTPException(404, "上传不存在")
    if data["expires_at"] < time.time():
        raise HTTPException(410, "上传记录已过期，请重新上传")
    return data


def save(data):
    manifest, _ = paths(data["upload_id"])
    videos.save_json(manifest, data)


def public(data):
    result = {key: data[key] for key in ("upload_id", "size_bytes", "received_bytes",
            "chunk_size", "chunks", "status", "result", "storage")}
    if data.get("error"):
        result["error"] = data["error"]
    return result


def ensure_drive_session(data):
    if data["storage"] == "google_drive" and not data.get("drive_session"):
        data["drive_session"] = drive_storage.begin(data)
        save(data)


def accept_drive_progress(data, offset, meta):
    pending = data.get("pending")
    if pending and offset == pending["start"] + pending["size"]:
        data["received_bytes"] = offset
        data["chunks"].append({"size": pending["size"], "sha256": pending["sha256"]})
        data["pending"] = None
    elif offset != data["received_bytes"] and not (pending and pending["start"] < offset < pending["start"] + pending["size"]):
        raise HTTPException(409, "Drive 续传位置与本地记录不一致")
    data["drive_offset"] = offset
    if meta:
        drive_storage.verify_metadata(meta, data)
        data["drive_file"] = meta
    data["expires_at"] = time.time() + TTL
    save(data)


def sync_drive(data):
    if data["storage"] == "google_drive" and data.get("pending"):
        offset, meta = drive_storage.progress(data)
        accept_drive_progress(data, offset, meta)


def create(filename, size, session_id, title, protect_existing, target_id=None, request_id=None):
    owner = owner_id()
    mode = storage_mode()
    if mode == "google_drive":
        drive_storage.configuration()
    with LOCK:
        folder = videos.MEDIA_ROOT.resolve() / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        pending = 0
        for manifest in folder.glob("*.json"):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data["expires_at"] < time.time():
                # Only our own generated partials and receipts are eligible.
                lock = upload_lock(manifest.stem)
                if lock.acquire(blocking=False):
                    try:
                        _, partial = paths(manifest.stem)
                        partial.unlink(missing_ok=True)
                        if data["storage"] == "local":
                            upload_limits.release_local(data["upload_id"])
                        manifest.unlink(missing_ok=True)
                    finally:
                        lock.release()
                continue
            if request_id and data["owner_id"] == owner and data.get("request_id") == request_id:
                if (data["filename"], data["size_bytes"], data.get("target_id")) != (filename, size, target_id):
                    raise HTTPException(409, "上传请求与已有文件不一致")
                ensure_drive_session(data)
                return public(data)
            if data["owner_id"] == owner and data["status"] != "complete":
                pending += 1
        if pending >= MAX_PENDING:
            raise HTTPException(429, "最多同时保留三个未完成上传，请续传或取消已有上传")
        upload_id = uuid.uuid4().hex
        upload_limits.reserve(upload_id, owner, size)
        _, partial = paths(upload_id)
        partial.parent.mkdir(parents=True, exist_ok=True)
        if mode == "local":
            partial.touch(exist_ok=False)
        data = {"upload_id": upload_id, "owner_id": owner, "filename": filename,
                "session_id": session_id, "title": title, "protect_existing": protect_existing,
                "target_id": target_id, "request_id": request_id,
                "storage": mode, "pending": None,
                "size_bytes": size, "received_bytes": 0, "chunk_size": CHUNK_SIZE,
                "chunks": [], "status": "uploading", "result": None,
                "expires_at": time.time() + TTL}
        try:
            save(data)
        except Exception:
            partial.unlink(missing_ok=True)
            if mode == "local":
                upload_limits.release_local(upload_id)
            raise
        ensure_drive_session(data)
        return public(data)


def status(upload_id):
    with upload_lock(upload_id):
        data = load(upload_id)
        sync_drive(data)
        if data["status"] == "complete":
            accounts.require("session", data["session_id"])
        return public(data)


def receive(upload_id, start, total, content, expected_hash):
    with upload_lock(upload_id):
        data = load(upload_id)
        sync_drive(data)
        if total != data["size_bytes"] or start % data["chunk_size"]:
            raise HTTPException(409, "视频大小或分片位置不一致")
        expected_size = min(data["chunk_size"], total - start)
        if start < 0 or expected_size <= 0 or len(content) != expected_size:
            raise HTTPException(422, "分片长度不正确")
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_hash:
            raise HTTPException(422, "分片校验失败，请重试")
        index = start // data["chunk_size"]
        if start < data["received_bytes"]:
            if data["chunks"][index] != {"size": len(content), "sha256": digest}:
                raise HTTPException(409, "分片与已接收内容不一致")
            return public(data)
        if data["status"] != "uploading" or start != data["received_bytes"]:
            raise HTTPException(409, "请从已接收位置继续上传")
        if data["storage"] == "google_drive":
            pending = {"start": start, "size": len(content), "sha256": digest}
            if data.get("pending") and data["pending"] != pending:
                raise HTTPException(409, "分片与 Drive 已接收内容不一致")
            data["pending"] = pending
            save(data)
            offset = data.get("drive_offset", start)
            received, meta = drive_storage.progress(data, content[offset - start:], offset)
            accept_drive_progress(data, received, meta)
            if received != start + len(content):
                raise HTTPException(503, "Drive 尚未接收完整分片，请重试")
            return public(data)
        _, partial = paths(upload_id)
        if not partial.exists() or partial.stat().st_size < start:
            raise HTTPException(409, "上传缓存不完整，请取消后重新上传")
        with partial.open("r+b") as target:
            # Metadata is authoritative if a previous write was interrupted.
            target.truncate(start)
            target.seek(start)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        data["received_bytes"] += len(content)
        data["chunks"].append({"size": len(content), "sha256": digest})
        data["expires_at"] = time.time() + TTL
        save(data)
        return public(data)


def finish(upload_id, resolve_target=None):
    with upload_lock(upload_id):
        data = load(upload_id)
        if data.get("target_id") and resolve_target and resolve_target(data["target_id"]) != data["session_id"]:
            raise HTTPException(409, "目标视频关联已变化，请重新选择上传目标")
        if data["status"] == "complete":
            # A receipt must not restore access to a now-hidden resource.
            accounts.require("session", data["session_id"])
            return data["result"]
        if data["received_bytes"] != data["size_bytes"]:
            raise HTTPException(409, "视频尚未上传完整")
        session_id = data["session_id"]
        if accounts.owner("session", session_id) or session_id in videos.registry():
            accounts.require("session", session_id)
        _, partial = paths(upload_id)
        output = partial.with_suffix(Path(data["filename"]).suffix.lower())
        # A completed rename/register may precede a lost completion response.
        if not partial.exists() and data.get("digest") and output.is_file():
            if output.stat().st_size != data["size_bytes"] or videos.file_sha256(output) != data["digest"]:
                raise HTTPException(409, "上传缓存校验失败")
            record = videos.registry().get(session_id)
            if record and Path(record["path"]).resolve() == output.resolve():
                result = {"match_id": session_id, "title": data["title"],
                          "video": videos.public_video(record), "reused_local_file": False}
                data.update(status="complete", result=result, expires_at=time.time() + TTL)
                save(data)
                return result
            output.replace(partial)
        if not partial.exists() or partial.stat().st_size < data["size_bytes"]:
            raise HTTPException(409, "上传缓存不完整，请取消后重新上传")
        with partial.open("r+b") as target:
            target.truncate(data["size_bytes"])
        digest = videos.file_sha256(partial)
        data.update(status="finalizing", digest=digest, expires_at=time.time() + TTL)
        save(data)
        try:
            record, reused = videos.import_original(partial, output, session_id, data["filename"],
                                                    data["title"], data["size_bytes"], digest,
                                                    data["protect_existing"])
        except videos.VideoNameConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = {"match_id": session_id, "title": data["title"],
                  "video": videos.public_video(record), "reused_local_file": reused}
        data.update(status="complete", result=result, expires_at=time.time() + TTL)
        save(data)
        partial.unlink(missing_ok=True)
        return result


def drive_finish_job(upload_id, resolve_target):
    try:
        with upload_lock(upload_id):
            data = load(upload_id)
        result = drive_storage.register(data, resolve_target)
        with upload_lock(upload_id):
            data = load(upload_id)
            data.update(status="complete", result=result, error=None, expires_at=time.time() + TTL)
            save(data)
    except Exception as error:
        with upload_lock(upload_id):
            data = load(upload_id)
            message = error.detail if isinstance(error, HTTPException) else (
                str(error) if isinstance(error, videos.VideoNameConflict) else "视频验证失败，请检查网络、磁盘空间和视频格式后重试")
            data.update(status="failed", error=message)
            save(data)
    finally:
        with upload_lock(upload_id):
            FINISHING.pop(upload_id, None)


def start_finish(upload_id, resolve_target=None):
    with upload_lock(upload_id):
        data = load(upload_id)
        if data["storage"] == "local":
            return finish(upload_id, resolve_target), False
        sync_drive(data)
        if data["status"] == "complete":
            accounts.require("session", data["session_id"])
            return data["result"], False
        if data["received_bytes"] != data["size_bytes"] or not data.get("drive_file"):
            raise HTTPException(409, "视频尚未完整上传到 Drive")
        if data.get("target_id") and resolve_target and resolve_target(data["target_id"]) != data["session_id"]:
            raise HTTPException(409, "目标视频关联已变化")
        if upload_id not in FINISHING:
            data.update(status="finalizing", error=None, expires_at=time.time() + TTL)
            save(data)
            context = copy_context()
            FINISHING[upload_id] = FINALIZERS.submit(context.run, drive_finish_job, upload_id, resolve_target)
        return public(data), True


def cancel(upload_id):
    with upload_lock(upload_id):
        data = load(upload_id)
        if data["status"] == "finalizing":
            raise HTTPException(409, "视频正在验证，请等待完成后再操作")
        if data["status"] == "complete":
            raise HTTPException(409, "已完成的视频请通过视频管理操作")
        manifest, partial = paths(upload_id)
        partial.unlink(missing_ok=True)
        if data["storage"] == "local":
            upload_limits.release_local(upload_id)
        manifest.unlink(missing_ok=True)
        return {"cancelled": True}
