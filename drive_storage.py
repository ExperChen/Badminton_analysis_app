"""Private administrator Drive storage. Credentials never reach website clients."""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException
import accounts

API = "https://www.googleapis.com/drive/v3/files"
FIELDS = "id,name,size,md5Checksum,mimeType,modifiedTime,parents,appProperties,capabilities(canDownload),trashed"
AUTH_LOCK = threading.RLock()
TOKEN = {}
CACHE_LOCK = threading.RLock()
FOLDER_LOCK = threading.RLock()
FOLDER_MIME = "application/vnd.google-apps.folder"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credentials_path():
    return Path(os.environ.get("GGAB_DRIVE_CREDENTIALS_PATH", Path(__file__).parent / ".secrets" / "google-drive.json"))


def configuration():
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
        if not all(data.get(key) for key in ("client_id", "client_secret", "refresh_token", "folder_id")):
            raise ValueError()
        file_id(data["folder_id"])
        return data
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "管理员尚未连接 Google Drive，请完成 Drive 存储配置") from None


def file_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value):
        raise HTTPException(502, "Drive 文件标识无效")
    return value


def token(force=False):
    with AUTH_LOCK:
        config = configuration()
        key = (str(credentials_path().resolve()), credentials_path().stat().st_mtime_ns)
        if not force and TOKEN.get("key") == key and TOKEN.get("expires", 0) > time.time() + 60:
            return TOKEN["value"]
        body = urlencode({key: config[key] for key in ("client_id", "client_secret", "refresh_token")}
                         | {"grant_type": "refresh_token"}).encode()
        try:
            req = Request("https://oauth2.googleapis.com/token", data=body,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                result = json.load(response)
            TOKEN.update(key=key, value=result["access_token"], expires=time.time() + result.get("expires_in", 3600))
            return TOKEN["value"]
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            raise HTTPException(503, "管理员的 Drive 授权已失效或网络不可达，请重新连接") from None


def open_api(url, method="GET", body=None, headers=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.googleapis.com":
        raise HTTPException(502, "Drive 请求地址无效")
    for attempt in range(2):
        request_headers = {"Authorization": "Bearer " + token(force=bool(attempt)), **(headers or {})}
        try:
            return build_opener(NoRedirect).open(Request(url, data=body, headers=request_headers, method=method), timeout=90)
        except HTTPError as error:
            if error.code == 401 and attempt == 0:
                error.close()
                continue
            if error.code in (200, 201, 308, 404, 416):
                return error
            code = error.code
            error.close()
            raise HTTPException(503, f"Drive 请求失败（HTTP {code}），请检查管理员授权、空间和网络") from None
        except (URLError, OSError):
            raise HTTPException(503, "暂时无法连接 Google Drive，可稍后续传") from None


def read_json(response):
    with response:
        content = response.read(2 * 1024 * 1024)
    try:
        return json.loads(content) if content else {}
    except ValueError:
        raise HTTPException(502, "Drive 返回了无效数据") from None


def metadata(identifier):
    response = open_api(API + "/" + file_id(identifier) + "?" + urlencode({"fields": FIELDS, "supportsAllDrives": "true"}))
    if response.code == 404:
        response.close()
        raise HTTPException(404, "Drive 原视频不存在或管理员已失去读取权限")
    return read_json(response)


def query_value(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def list_files(query):
    page = None
    while True:
        params = {"q": query, "fields": f"nextPageToken,files({FIELDS})", "pageSize": 100}
        if page:
            params["pageToken"] = page
        result = read_json(open_api(API + "?" + urlencode(params)))
        yield from result.get("files", [])
        page = result.get("nextPageToken")
        if not page:
            return


def account_folder(owner_id):
    """Bind a private child folder to an immutable local account ID, never its name."""
    root = file_id(configuration()["folder_id"])
    with FOLDER_LOCK, accounts.database() as connection:
        # The migration CLI and the website can initialize folders simultaneously.
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute("SELECT email FROM users WHERE id=?", (owner_id,)).fetchone()
        key = f"drive.folder.{root}.{owner_id}"
        saved = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not user and (accounts.ENFORCE_AUTH or owner_id != "local"):
            raise HTTPException(409, "视频账号不存在，未创建 Drive 文件夹")
        folder = None
        if saved:
            try:
                folder = metadata(saved[0])
            except HTTPException as error:
                if error.status_code != 404:
                    raise
            if folder and folder.get("trashed"):
                folder = None
        if folder is None:
            query = (f"'{query_value(root)}' in parents and trashed = false and mimeType = '{FOLDER_MIME}' "
                     f"and appProperties has {{ key='ggab_owner' and value='{query_value(owner_id)}' }} "
                     "and appProperties has { key='ggab_kind' and value='account_videos' }")
            matches = list(list_files(query))
            if len(matches) > 1:
                raise HTTPException(409, "发现重复账号文件夹，请管理员核对")
            if matches:
                folder = matches[0]
            else:
                label = user["email"] if user else "local"
                label = re.sub(r'[\\/\x00-\x1f]', "_", label)[:120]
                payload = {"name": f"{label}__{owner_id[:8]}", "mimeType": FOLDER_MIME,
                           "parents": [root], "appProperties": {"ggab_owner": owner_id, "ggab_kind": "account_videos"}}
                folder = read_json(open_api(API + "?" + urlencode({"fields": FIELDS}), "POST",
                                            json.dumps(payload).encode(), {"Content-Type": "application/json"}))
        if (folder.get("mimeType") != FOLDER_MIME or folder.get("trashed") or
                folder.get("parents") != [root] or folder.get("appProperties", {}).get("ggab_owner") != owner_id or
                folder.get("appProperties", {}).get("ggab_kind") != "account_videos"):
            raise HTTPException(409, "Drive 账号文件夹归属或位置已变化，未写入视频")
        identifier = file_id(folder.get("id"))
        connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, identifier))
        return identifier


def organize_file(meta, owner_id):
    """Move a registered video without changing its ID or its content."""
    folder = account_folder(owner_id)
    properties = meta.get("appProperties", {})
    if meta.get("parents") == [folder] and properties.get("ggab_owner") == owner_id:
        return meta
    params = {"fields": FIELDS, "supportsAllDrives": "true"}
    if folder not in meta.get("parents", []):
        params["addParents"] = folder
    old = [parent for parent in meta.get("parents", []) if parent != folder]
    if old:
        params["removeParents"] = ",".join(old)
    payload = {"appProperties": {**properties, "ggab_owner": owner_id}}
    result = read_json(open_api(API + "/" + file_id(meta["id"]) + "?" + urlencode(params), "PATCH",
                                json.dumps(payload).encode(), {"Content-Type": "application/json"}))
    if (result.get("id") != meta["id"] or result.get("parents") != [folder] or
            result.get("md5Checksum") != meta.get("md5Checksum") or
            result.get("appProperties", {}).get("ggab_owner") != owner_id):
        raise HTTPException(409, "Drive 整理结果未通过核验")
    return result


def begin(data):
    data["drive_folder_id"] = account_folder(data["owner_id"])
    mime = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
            ".mkv": "video/x-matroska", ".webm": "video/webm", ".avi": "video/x-msvideo"}[Path(data["filename"]).suffix.lower()]
    payload = {"name": data["filename"], "parents": [data["drive_folder_id"]],
               "mimeType": mime,
               "appProperties": {"ggab_upload_id": data["upload_id"], "ggab_owner": data["owner_id"]}}
    url = "https://www.googleapis.com/upload/drive/v3/files?" + urlencode({"uploadType": "resumable", "fields": FIELDS})
    response = open_api(url, "POST", json.dumps(payload).encode(),
                        {"Content-Type": "application/json", "X-Upload-Content-Type": mime,
                         "X-Upload-Content-Length": str(data["size_bytes"])})
    with response:
        location = response.headers.get("Location", "")
    parsed = urlsplit(location)
    if parsed.scheme != "https" or parsed.netloc != "www.googleapis.com" or not parsed.path.startswith("/upload/"):
        raise HTTPException(502, "Drive 未返回有效的续传会话")
    return location


def progress(data, content=None, start=None):
    span = f"bytes */{data['size_bytes']}" if content is None else f"bytes {start}-{start + len(content) - 1}/{data['size_bytes']}"
    response = open_api(data["drive_session"], "PUT", b"" if content is None else content,
                        {"Content-Type": "application/octet-stream", "Content-Range": span})
    if response.code == 404:
        response.close()
        # A successfully completed session can disappear before its response is recorded.
        query = ("trashed = false and "
                 f"appProperties has {{ key='ggab_upload_id' and value='{query_value(data['upload_id'])}' }} and "
                 f"appProperties has {{ key='ggab_owner' and value='{query_value(data['owner_id'])}' }}")
        found = read_json(open_api(API + "?" + urlencode({"q": query, "fields": f"files({FIELDS})", "pageSize": 2}))).get("files", [])
        if len(found) != 1:
            raise HTTPException(410, "Drive 续传会话已过期，请重新上传")
        return int(found[0].get("size", -1)), found[0]
    if response.code == 308:
        with response:
            received = response.headers.get("Range", "")
        match = re.fullmatch(r"bytes=0-(\d+)", received)
        if received and not match:
            raise HTTPException(502, "Drive 返回了无效的续传位置")
        return int(match[1]) + 1 if match else 0, None
    result = read_json(response)
    return int(result.get("size", -1)), result


def verify_metadata(meta, data):
    properties = meta.get("appProperties", {})
    if (meta.get("trashed") or int(meta.get("size", -1)) != data["size_bytes"] or
            properties.get("ggab_upload_id") != data["upload_id"] or properties.get("ggab_owner") != data["owner_id"]):
        raise HTTPException(409, "Drive 文件与上传记录不一致，未建立视频关联")
    file_id(meta.get("id"))
    if not re.fullmatch(r"[0-9a-f]{32}", meta.get("md5Checksum", "")):
        raise HTTPException(502, "Drive 尚未返回视频校验信息，请稍后重试")


def verify_source(record):
    meta = metadata(record["drive_file_id"])
    if (meta.get("trashed") or meta.get("md5Checksum") != record["drive_md5"] or
            int(meta.get("size", -1)) != record["drive_size"]):
        raise HTTPException(409, "Drive 原视频已变化，请重新关联后再处理")
    if meta.get("capabilities", {}).get("canDownload") is False:
        raise HTTPException(403, "管理员的 Drive 账号不能下载这段视频")
    return meta


def media_response(record, byte_range=None):
    verify_source(record)
    headers = {}
    if byte_range:
        if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range):
            raise HTTPException(416, "仅支持一个有效的视频字节范围")
        headers["Range"] = byte_range
    response = open_api(API + "/" + file_id(record["drive_file_id"]) + "?alt=media", headers=headers)
    if response.code not in (200, 206):
        code = response.code
        response.close()
        raise HTTPException(416 if code == 416 else 502, "Drive 视频范围不可读取")
    return response


def cached(record):
    """Download a checked cache only for local processing, preserving source timestamps."""
    import video_tools as videos
    verify_source(record)
    with CACHE_LOCK:
        path = Path(record["path"])
        root = (videos.MEDIA_ROOT / "drive-cache").resolve()
        retained = record.get("retained_local_path") == str(path) and path.resolve().is_relative_to(videos.MEDIA_ROOT.resolve())
        if path.resolve().parent != root and not retained:
            raise HTTPException(409, "视频缓存目录无效")
        marker = path.with_suffix(path.suffix + (".drive-cache.json" if retained else ".json"))
        if path.is_file() and marker.is_file():
            saved = json.loads(marker.read_text(encoding="utf-8"))
            stat = path.stat()
            if saved == {"md5": record["drive_md5"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns} and stat.st_size == record["drive_size"]:
                return path
        if retained and path.exists():
            raise HTTPException(409, "保留的本机原视频已变化或校验标记缺失，请管理员核对；未覆盖原件")
        root.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = root / (uuid.uuid4().hex + ".download")
        digest = hashlib.md5()
        total = 0
        try:
            with media_response(record) as response, temporary.open("xb") as target:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > record["drive_size"]:
                        raise HTTPException(409, "Drive 下载大小不一致")
                    digest.update(chunk)
                    target.write(chunk)
            if total != record["drive_size"] or digest.hexdigest() != record["drive_md5"]:
                raise HTTPException(409, "Drive 视频下载校验失败")
            if retained:
                # Publishing a retained original must not overwrite a file created meanwhile.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise HTTPException(409, "本机原视频在恢复期间出现，未覆盖原件") from None
            else:
                temporary.replace(path)
            if record.get("cache_mtime_ns"):
                os.utime(path, ns=(record["cache_mtime_ns"], record["cache_mtime_ns"]))
            videos.save_json(marker, {"md5": record["drive_md5"], "size": total, "mtime_ns": path.stat().st_mtime_ns})
            return path
        finally:
            temporary.unlink(missing_ok=True)


def verify_upload(path, data):
    import video_tools as videos
    digest, pending = hashlib.sha256(), deque()
    workers = max(1, videos.CPU_THREADS - 1)
    def check_one():
        future, expected = pending.popleft()
        if future.result().hexdigest() != expected:
            raise HTTPException(409, "Drive 原视频与已上传片段不一致，未建立关联")
    with videos.ENCODING_LOCK, ThreadPoolExecutor(max_workers=workers) as pool, path.open("rb") as source:
        for chunk_info in data["chunks"]:
            content = source.read(chunk_info["size"])
            if len(content) != chunk_info["size"]:
                raise HTTPException(409, "Drive 原视频长度与分片不一致")
            digest.update(content)
            pending.append((pool.submit(hashlib.sha256, content), chunk_info["sha256"]))
            if len(pending) >= workers:
                check_one()
        while pending:
            check_one()
        if source.read(1):
            raise HTTPException(409, "Drive 原视频长度与分片不一致")
    return digest.hexdigest()


def register(data, resolve_target=None):
    import video_tools as videos
    meta = metadata(data["drive_file"]["id"])
    verify_metadata(meta, data)
    if meta["md5Checksum"] != data["drive_file"]["md5Checksum"]:
        raise HTTPException(409, "Drive 原视频在验证前已变化，未建立关联")
    path = (videos.MEDIA_ROOT / "drive-cache" / (meta["id"] + Path(data["filename"]).suffix.lower())).resolve()
    remote = {"storage": "google_drive", "path": str(path), "drive_file_id": meta["id"],
              "drive_md5": meta["md5Checksum"], "drive_size": data["size_bytes"]}
    cached(remote)
    digest = verify_upload(path, data)
    info = videos.probe(path)
    with videos.LOCK:
        if accounts.ENFORCE_AUTH:
            with accounts.database() as connection:
                user = connection.execute("SELECT enabled FROM users WHERE id=?", (data["owner_id"],)).fetchone()
            if not user or not user["enabled"]:
                raise HTTPException(403, "上传账号已停用，未建立视频关联")
        if data.get("target_id") and resolve_target and resolve_target(data["target_id"]) != data["session_id"]:
            raise HTTPException(409, "目标视频关联已变化，未建立关联")
        old = videos.registry().get(data["session_id"])
        preserve_version = False
        if old and data["protect_existing"]:
            _, old_path = videos.source(data["session_id"])
            if old_path.stat().st_size != data["size_bytes"] or videos.file_sha256(old_path) != digest:
                raise videos.VideoNameConflict("同名视频内容不同，请在目标 Session 中明确选择更换原视频")
            preserve_version = old["filename"] == data["filename"] and old["duration"] == info["duration"] and old["has_audio"] == info["has_audio"]
        record = videos.register(data["session_id"], path, data["filename"], info, data["title"])
        records = videos.registry()
        record = {**record, **remote, "sha256": digest, "cache_mtime_ns": path.stat().st_mtime_ns}
        if preserve_version:
            record["updated_at"] = old["updated_at"]
        records[data["session_id"]] = record
        videos.save_json(videos.MEDIA_ROOT / "sessions.json", records)
    return {"match_id": data["session_id"], "title": data["title"], "video": videos.public_video(record),
            "reused_local_file": False, "storage": "google_drive"}
