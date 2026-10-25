"""FastAPI entry point. Run: python -m uvicorn app:app --reload."""

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import contextmanager, asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette._utils import get_route_path

from analytics import analyze
import video_tools as videos
import local_data
import playback_preview
import video_uploads
import drive_storage
import accounts
import service_logging
import security_config
import feedback
import feedback_mail
from tracker import router as tracker_router, database as tracker_database, saved_player_options

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("YUXU_DB_PATH", ROOT / "ggab.db")).resolve()
@asynccontextmanager
async def lifespan(application):
    feedback_mail.start()
    try:
        yield
    finally:
        feedback_mail.stop()


app = FastAPI(title="羽球分析", version="0.1.0", lifespan=lifespan)
logger = logging.getLogger("badminton.service")
service_logging.configure()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
app.include_router(tracker_router)
app.include_router(accounts.router)
app.include_router(feedback.router)
ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


@app.get("/feedback", response_class=HTMLResponse)
def feedback_page(request: Request):
    return page(request, "feedback.html")


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return page(request, "help.html")


@app.get("/tracker", response_class=HTMLResponse)
@app.get("/tracker/new", response_class=HTMLResponse)
@app.get("/tracker/projects/{project_id}", response_class=HTMLResponse)
def tracker_page(request: Request):
    return page(request, "tracker.html")


@app.get("/api/tracker/player-options")
def tracker_players():
    combined = {}
    for item in query("SELECT DISTINCT striker_name AS name, striker_handedness AS handedness, 0 AS is_you FROM shots WHERE striker_name IS NOT NULL ORDER BY striker_name", personal=True):
        name = " ".join(str(item.get("name") or "").split())
        if name:
            combined[name.casefold()] = {**item, "name": name, "is_you": 0}
    for item in saved_player_options():
        key = item["name"].casefold()
        if key in combined:
            combined[key].update({k: v for k, v in item.items() if v is not None})
        else:
            combined[key] = item
    return sorted(combined.values(), key=lambda item: item["name"].casefold())


@app.middleware("http")
async def local_mutations(request, call_next):
    if accounts.ENFORCE_AUTH and request.url.hostname not in security_config.allowed_hosts():
        return JSONResponse({"detail": "请使用本机地址访问"}, status_code=403)
    if (security_config.public_origin() and request.url.hostname not in security_config.LOCAL_HOSTS
            and (request.url.scheme != "https" or f"{request.url.scheme}://{request.url.netloc}" != security_config.public_origin())):
        return JSONResponse({"detail": "请使用已配置的 HTTPS 网站地址访问"}, status_code=403)
    if (os.environ.get("YUXU_REQUIRE_VPN_IDENTITY") == "1"
            and request.url.hostname not in security_config.LOCAL_HOSTS
            and not request.headers.get("tailscale-user-login")):
        return JSONResponse({"detail": "请通过已登录的私有网络访问"}, status_code=403)
    route_path = get_route_path(request.scope)
    user = accounts.authenticate(request) if accounts.ENFORCE_AUTH else None
    public = route_path in {"/login", "/health", "/api/auth/status", "/api/auth/setup", "/api/auth/register", "/api/auth/login", "/api/auth/password-reset/complete"} or route_path.startswith("/static/")
    if accounts.ENFORCE_AUTH and not public and not user:
        if not route_path.startswith("/api/"):
            return RedirectResponse(request.scope.get("root_path", "") + "/login", status_code=303)
        return JSONResponse({"detail": "请登录本地账号", "code": "LOGIN_REQUIRED"}, status_code=401)
    if route_path in {'/docs', '/redoc', '/openapi.json'} and accounts.ENFORCE_AUTH and user.get('role') != 'admin':
        return JSONResponse({'detail': '仅管理员可访问接口诊断资料'}, status_code=403)
    if request.method in {"GET", "HEAD"} and (
        route_path == "/api/my/export.csv"
        or re.fullmatch(r"/api/matches/[^/]+/export", route_path)
        or re.fullmatch(r"/api/bridge/projects/[^/]+/shots\.csv", route_path)
    ):
        return JSONResponse({"detail": "数据文件导出已关闭"}, status_code=403,
                            headers={"Cache-Control": "no-store"})
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        if request.headers.get("x-yuxu-local") != "1" or (
            origin and origin != f"{request.url.scheme}://{request.url.netloc}"
        ):
            return JSONResponse({"detail": "请从本地网站发起这个操作。"}, status_code=403)
        if user and not public and not secrets_compare(request.headers.get("x-csrf-token", ""), user["csrf"]):
            return JSONResponse({"detail": "页面验证已失效，请刷新后重试"}, status_code=403)
    token = accounts.principal.set(user)
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_unhandled method=%s path=%s user=%s", request.method,
                             route_path, (user or {}).get("id", "anonymous"))
            raise
        if response.status_code >= 400:
            logger.warning("request_failed method=%s path=%s status=%s user=%s", request.method,
                           route_path, response.status_code, (user or {}).get("id", "anonymous"))
        if accounts.ENFORCE_AUTH and not route_path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        accounts.principal.reset(token)


def secrets_compare(first, second):
    import secrets
    return secrets.compare_digest(first, second)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if accounts.current() and request.query_params.get("new") != "1":
        return RedirectResponse(request.scope.get("root_path", "") + "/insights/match-breakdown", status_code=303)
    if accounts.ENFORCE_AUTH and (not accounts.login_context(request) or request.query_params.get("new") == "1"):
        return RedirectResponse(f"/s/{uuid.uuid4().hex}/login", status_code=303)
    return page(request, "accounts_login.html")


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    return page(request, "accounts.html")


@app.get("/api/admin/logs")
def admin_logs():
    accounts.admin_required()
    return {"lines": service_logging.recent_lines()}


def page(request, name):
    response = templates.TemplateResponse(request=request, name=name, context={})
    prefix = request.scope.get("root_path", "")
    if not prefix:
        return response
    html = re.sub(r'''((?:href|src)\s*=\s*["'])/(?!/)''', lambda match: match[1] + prefix + "/", response.body.decode("utf-8"))
    return HTMLResponse(html)


@contextmanager
def owned_database(personal=False):
    with closing(sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        allowed = accounts.allowed("match", personal=personal)
        if allowed is not None:
            conn.execute("CREATE TEMP TABLE allowed_matches(id TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO allowed_matches VALUES(?)", ((mid,) for mid in allowed))
            conn.execute("CREATE TEMP VIEW shots AS SELECT * FROM main.shots WHERE match_id IN (SELECT id FROM allowed_matches)")
        yield conn


def query(sql, params=(), *, personal=False):
    # Read-only URI prevents accidental database creation or modification.
    try:
        with owned_database(personal=personal) as conn:
            return [dict(row) for row in conn.execute(sql, params)]
    except sqlite3.Error as exc:
        raise HTTPException(503, "无法读取 shots 数据，请检查数据库路径与表结构。") from exc


def canonical_match(match_id):
    mappings = local_data.aliases(DB_PATH)
    if match_id in mappings:
        return match_id
    matched = [remote for remote, session in mappings.items() if session == match_id]
    return matched[0] if len(matched) == 1 else match_id


def video_session(match_id):
    resolved = canonical_match(match_id)
    mapping = local_data.aliases(DB_PATH)
    if resolved in mapping:
        accounts.require("match", resolved)
    elif not accounts.visible("match", resolved):
        accounts.require("session", match_id)
    return mapping.get(resolved, match_id)


def visible_records():
    return {sid: record for sid, record in videos.registry().items() if accounts.visible("session", sid)}


def match_rows(match_id, game=None):
    match_id = canonical_match(match_id)
    if not query("SELECT 1 FROM shots WHERE match_id = ? LIMIT 1", (match_id,)):
        if match_id in visible_records():
            return []
        raise HTTPException(404, "找不到这场比赛。")
    sql = "SELECT * FROM shots WHERE match_id = ?"
    params = [match_id]
    if game is not None:
        sql += " AND game_number = ?"
        params.append(game)
    return query(sql + " ORDER BY game_number, rally_number_in_game, rally_number, "
                 "rally_id, shot_number_in_rally, video_timestamp_seconds, shot_id", params)


@app.get("/", include_in_schema=False)
def home(request: Request):
    return RedirectResponse(request.scope.get("root_path", "") + "/insights/match-breakdown")


@app.get("/insights/match-breakdown", response_class=HTMLResponse)
def breakdown(request: Request):
    return page(request, "index.html")


@app.get("/tools/video-cutter", response_class=HTMLResponse)
def video_cutter(request: Request):
    return page(request, "video_cutter.html")


@app.get("/api/matches")
def matches():
    result = query("SELECT match_id, MAX(match_title) AS title, MAX(match_date) AS date, "
                 "MAX(game_type) AS game_type, COUNT(*) AS shots, "
                 "COUNT(DISTINCT rally_id) AS rallies FROM shots GROUP BY match_id "
                 "ORDER BY date DESC, title DESC, match_id")
    records = visible_records()
    mappings = local_data.aliases(DB_PATH)
    project_references = tracker_session_references()
    known = {m["match_id"] for m in result}
    linked_sessions = {session for remote, session in mappings.items() if remote in known}
    for session_id, record in records.items():
        if session_id not in known and session_id not in linked_sessions:
            result.append({"match_id": session_id, "title": record["title"], "date": "",
                           "game_type": None, "shots": 0, "rallies": 0})
    for item in result:
        session_id = mappings.get(item["match_id"], item["match_id"])
        record = records.get(session_id)
        exists = bool(record and (record.get("storage") == "google_drive" or Path(record["path"]).is_file()))
        item["video"] = videos.public_video(record) if exists else None
        item["has_video"] = exists
        item["tagged"] = item["shots"] > 0
        item["local_session_id"] = session_id
        item["can_delete"] = bool(record) and not item["tagged"] and not project_references.get(session_id)
    return result


@app.get("/api/data-revision")
def data_revision():
    saved = local_data.revision(DB_PATH)
    return {"revision": saved.get("revision"), "imported_at": saved.get("imported_at")}


def tracker_session_references():
    """Include recoverable projects; a Session must not be removed underneath one."""
    references = {}
    with tracker_database() as conn:
        for table in ("projects", "deleted_projects"):
            for row in conn.execute(f"SELECT id,payload FROM {table}"):
                session_id = json.loads(row["payload"]).get("session_id")
                if session_id:
                    references.setdefault(session_id, set()).add(row["id"])
    return references


@app.delete("/api/sessions/{session_id}")
def delete_video_session(session_id: str):
    accounts.require("session", session_id)
    if query("SELECT 1 FROM shots WHERE match_id=? LIMIT 1", (session_id,)):
        raise HTTPException(409, "这场比赛已有击球标注，不能从这里删除视频。")
    if tracker_session_references().get(session_id):
        raise HTTPException(409, "这个视频 Session 仍被本地 Match Tracker 项目引用，不能删除；可使用安全释放重复副本。")
    try:
        disposition = videos.remove_unmatched_session(session_id)
    except KeyError:
        raise HTTPException(404, "这个视频 Session 已不存在。")
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": True, "video_retained": disposition in ("retained_shared", "retained_original", "retained_drive"),
            "message": "已移除视频 Session；原片仍被其他 Session 使用，文件已保留。" if disposition == "retained_shared"
                       else "已移除视频 Session；管理员 Drive 中的原视频已保留。" if disposition == "retained_drive"
                       else "已移除视频 Session；原片位于本地视频目录根目录，文件已保留。" if disposition == "retained_original"
                       else "已删除未匹配的视频 Session，视频副本已移入本地回收目录。"}


@app.get("/api/matches/{match_id}/breakdown")
def data(match_id: str, game: int | None = Query(None, ge=1),
         perspective: str = Query("you", pattern="^(you|opponent)$")):
    match_id = canonical_match(match_id)
    rows = match_rows(match_id, game)
    analysis_rows, perspective_labels = local_analysis_view(match_id, rows)
    result = analyze(analysis_rows, perspective)
    result["perspective_labels"] = perspective_labels
    if accounts.ENFORCE_AUTH:
        result["warnings"].append("观察方按比赛项目选择；没有项目设置时沿用已有球员标注。上传者身份不用于推断球员。")
    result["games"] = [r["game_number"] for r in query(
        "SELECT DISTINCT game_number FROM shots WHERE match_id=? "
        "AND game_number IS NOT NULL ORDER BY game_number", (match_id,))]
    result["match_id"] = match_id
    record = videos.registry().get(video_session(match_id))
    result["video"] = videos.public_video(record) if record and (record.get("storage") == "google_drive" or Path(record["path"]).is_file()) else None
    return result


def local_analysis_view(match_id, rows):
    """Derive a team perspective without changing stored shot records."""
    if not accounts.ENFORCE_AUTH:
        return rows, {"you": "观察方", "opponent": "另一方"}
    with tracker_database() as conn:
        pid = local_data.project_matches(DB_PATH).get(match_id)
        project_row = conn.execute("SELECT id,payload FROM projects WHERE id=?", (pid,)).fetchone()
    if not project_row or not accounts.visible("project", project_row["id"]):
        return rows, {"you": "观察方", "opponent": "另一方"}
    saved = json.loads(project_row["payload"])
    players = saved.get("players", [])
    if not players:
        return rows, {"you": "观察方", "opponent": "另一方"}
    selected = next((p["slot"][0] for p in players if p.get("is_you")), "A")
    groups = {}
    for row in rows:
        groups.setdefault((row["match_id"], row["rally_id"]), []).append(row)
    derived = []
    for shots in groups.values():
        first, last = shots[0], shots[-1]
        winner = None
        try:
            da = last["score_a_after"] - first["score_a_before"]
            db = last["score_b_after"] - first["score_b_before"]
            if (da, db) in {(1, 0), (0, 1)}:
                winner = int(("A" if da == 1 else "B") == selected)
        except TypeError:
            pass
        derived.extend({**r, "striker_is_you": int(str(r.get("striker", "")).startswith(selected)),
                        "rally_won_by_you": winner} for r in shots)
    return derived, {"you": selected + " 方", "opponent": ("B" if selected == "A" else "A") + " 方"}


@app.get("/api/video-tools")
def video_tools_status():
    try:
        videos.binary("ffmpeg")
        videos.binary("ffprobe")
        return {"available": True}
    except ValueError as exc:
        return {"available": False, "error": str(exc)}


def target_video_session(filename, match_id):
    existing = matches()
    if match_id:
        match_id = canonical_match(match_id)
        if not any(m["match_id"] == match_id for m in existing):
            raise HTTPException(404, "找不到要关联的 session。")
    else:
        same = [m for m in existing if Path(m["title"]).stem.casefold() == Path(filename).stem.casefold()]
        if len(same) > 1:
            raise HTTPException(409, "存在多场同名比赛，请在目标 session 中选择视频。")
        match_id = same[0]["match_id"] if same else "video-" + uuid.uuid4().hex
    title = next((m["title"] for m in existing if m["match_id"] == match_id), filename)
    existing_id = any(m["match_id"] == match_id for m in existing)
    return (video_session(match_id) if existing_id else match_id), title


@app.get("/api/sessions/local-videos")
def local_video_candidates():
    if not accounts.privileged():
        return {"directory": "", "videos": []}
    return {"directory": str(videos.MEDIA_ROOT), "videos": videos.available_local_originals()}


class LinkLocalVideo(BaseModel):
    relative_path: str = Field(min_length=1, max_length=500)
    match_id: str | None = None


@app.post("/api/sessions/{session_id}/reclaim-duplicate")
async def reclaim_duplicate_video(session_id: str):
    accounts.admin_required()
    accounts.require("session", session_id)
    references = tracker_session_references().get(session_id, set())
    try:
        return await run_in_threadpool(videos.reclaim_duplicate_original, session_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/sessions/link")
async def link_local_video(body: LinkLocalVideo):
    accounts.admin_required()
    filename = Path(body.relative_path.replace("\\", "/")).name
    match_id, title = target_video_session(filename, body.match_id)
    try:
        record = await run_in_threadpool(videos.link_local_original, body.relative_path,
                                         match_id, filename, title, body.match_id is None)
    except videos.VideoNameConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"match_id": match_id, "title": title, "video": videos.public_video(record),
            "reused_local_file": True}


class StartVideoUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(gt=0, le=video_uploads.MAX_SIZE, strict=True)
    match_id: str | None = None
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@app.post("/api/uploads", status_code=201)
def start_video_upload(body: StartVideoUpload):
    filename = Path(body.filename.replace("\\", "/")).name
    if Path(filename).suffix.lower() not in videos.VIDEO_EXTENSIONS:
        raise HTTPException(422, "请选择 MP4、MOV、MKV、WebM、AVI 或 M4V 视频。")
    session_id, title = target_video_session(filename, body.match_id)
    return video_uploads.create(filename, body.size_bytes, session_id, title, body.match_id is None,
                                body.match_id, body.request_id)


@app.get("/api/uploads/{upload_id}")
def video_upload_status(upload_id: str):
    return video_uploads.status(upload_id)


@app.put("/api/uploads/{upload_id}")
async def upload_video_chunk(upload_id: str, request: Request):
    data = await run_in_threadpool(video_uploads.status, upload_id)
    span = re.fullmatch(r"bytes (\d{1,12})-(\d{1,12})/(\d{1,12})", request.headers.get("content-range", ""))
    expected_hash = request.headers.get("x-chunk-sha256", "")
    if not span or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise HTTPException(422, "缺少有效的分片范围或 SHA-256 校验")
    start, end, total = map(int, span.groups())
    size = end - start + 1
    if size <= 0 or size > data["chunk_size"]:
        raise HTTPException(413, "分片超过允许大小")
    if (total != data["size_bytes"] or start % data["chunk_size"] or
            size != min(data["chunk_size"], total - start)):
        raise HTTPException(422, "分片范围与视频大小不一致")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > size:
            raise HTTPException(413, "分片超过声明长度")
        content.extend(chunk)
    return await run_in_threadpool(video_uploads.receive, upload_id, start, total, content, expected_hash)


@app.post("/api/uploads/{upload_id}/complete")
def complete_video_upload(upload_id: str):
    result, pending = video_uploads.start_finish(upload_id, video_session)
    return JSONResponse(result, status_code=202 if pending else 200)


@app.delete("/api/uploads/{upload_id}")
def cancel_video_upload(upload_id: str):
    return video_uploads.cancel(upload_id)


@app.post("/api/sessions/import", status_code=201)
async def import_video(request: Request, filename: str = Query(..., min_length=1, max_length=240),
                       match_id: str | None = None):
    if accounts.ENFORCE_AUTH or video_uploads.storage_mode() == "google_drive":
        raise HTTPException(409, "请刷新页面，通过分片上传保存视频并核验账号额度")
    explicit_session = match_id is not None
    filename = Path(filename.replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in videos.VIDEO_EXTENSIONS:
        raise HTTPException(422, "请选择 MP4、MOV、MKV、WebM、AVI 或 M4V 视频。")
    match_id, title = target_video_session(filename, match_id)
    folder = videos.MEDIA_ROOT / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    temp = folder / (file_id + ".upload")
    output = folder / (file_id + suffix)
    total = 0
    digest = hashlib.sha256()
    try:
        with temp.open("xb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 50 * 1024**3:
                    raise HTTPException(413, "单个视频超过 50 GB 限制。")
                digest.update(chunk)
                await run_in_threadpool(handle.write, chunk)
        if total == 0:
            raise HTTPException(422, "视频文件为空。")
        record, reused = await run_in_threadpool(videos.import_original, temp, output,
                                                  match_id, filename, title, total, digest.hexdigest(),
                                                  not explicit_session)
        return {"match_id": match_id, "title": title, "video": videos.public_video(record),
                "reused_local_file": reused}
    except videos.VideoNameConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)


@app.get("/api/matches/{match_id}/video")
def session_video(match_id: str, request: Request):
    session_id = video_session(match_id)
    accounts.require("session", session_id)
    record = videos.registry().get(session_id)
    if record and record.get("storage") == "google_drive":
        upstream = drive_storage.media_response(record, request.headers.get("range"))
        headers = {key: value for key in ("Content-Length", "Content-Range", "Accept-Ranges")
                   if (value := upstream.headers.get(key))}
        headers["Accept-Ranges"] = "bytes"
        def stream():
            try:
                while chunk := upstream.read(256 * 1024):
                    yield chunk
            finally:
                upstream.close()
        return StreamingResponse(stream(), status_code=upstream.code,
                                 media_type=upstream.headers.get("Content-Type", "video/mp4"), headers=headers)
    try:
        record, path = videos.source(session_id)
        return FileResponse(path, filename=record["filename"], content_disposition_type="inline")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/matches/{match_id}/playback-preview")
def playback_preview_status(match_id: str):
    try:
        return playback_preview.status(video_session(match_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/matches/{match_id}/playback-preview")
def generate_playback_preview(match_id: str):
    try:
        return playback_preview.start(video_session(match_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/matches/{match_id}/playback-preview/video")
def playback_preview_video(match_id: str):
    try:
        session = video_session(match_id)
        if playback_preview.status(session)["state"] != "ready":
            raise HTTPException(404, "流畅预览尚未生成")
        _, _, _, path = playback_preview.target(session)
        return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/matches/{match_id}/clip-plan")
def clip_plan(match_id: str, game: int | None = Query(None, ge=1),
              before: float = Query(0, ge=0, le=5, allow_inf_nan=False),
              after: float = Query(0, ge=0, le=5, allow_inf_nan=False)):
    rows = match_rows(match_id, game)
    try:
        record, _ = videos.source(video_session(match_id))
        return videos.plan_clips(rows, record["duration"], before, after)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class ClipRequest(BaseModel):
    game: int | None = Field(None, ge=1)
    before: float = Field(0, ge=0, le=5, allow_inf_nan=False)
    after: float = Field(0, ge=0, le=5, allow_inf_nan=False)


@app.post("/api/matches/{match_id}/clips", status_code=202)
def create_clip(match_id: str, settings: ClipRequest):
    rows = match_rows(match_id, settings.game)
    try:
        record, path = videos.source(video_session(match_id))
        # Probe again immediately before export, in case a registered file changed.
        info = videos.probe(path)
        plan = videos.plan_clips(rows, info["duration"], settings.before, settings.after)
        return videos.create_job(match_id, path, plan, settings.game, info["has_audio"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/matches/{match_id}/clips")
def list_clips(match_id: str):
    match_rows(match_id)
    return videos.session_jobs(match_id)


@app.get("/api/clips/{job_id}")
def clip_status(job_id: str):
    accounts.require("clip", job_id)
    try:
        return videos.public_job(videos.read_job(job_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/clips/{job_id}/download")
def clip_download(job_id: str):
    job = clip_status(job_id)
    if job["status"] != "complete":
        raise HTTPException(409, "剪辑尚未完成。")
    path = videos.job_path(job_id) / "rallies.mp4"
    if not path.is_file():
        raise HTTPException(404, "导出文件已不在本机，请重新生成。")
    return FileResponse(path, media_type="video/mp4", filename=f"rallies-{job_id[:8]}.mp4")


@app.get("/api/clips/{job_id}/timeline")
def clip_timeline(job_id: str):
    job = clip_status(job_id)
    if job["status"] != "complete":
        raise HTTPException(409, "剪辑尚未完成。")
    return FileResponse(videos.job_path(job_id) / "timeline.json", media_type="application/json", filename="timeline.json")


@app.get("/api/matches/{match_id}/export")
def export(match_id: str, game: int | None = Query(None, ge=1)):
    rows = match_rows(match_id, game)
    columns = [r["name"] for r in query("PRAGMA table_info(shots)")]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    # Spreadsheet programs may interpret leading formula characters as code.
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    writer.writerows({k: safe(v) for k, v in row.items()} for row in rows)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="match-shots.csv"'})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/my/export.csv")
def export_my_csv(match_id: str | None = None, game: int | None = Query(None, ge=1)):
    """Export complete rallies, including the opponent, from owned matches only."""
    clauses, params = [], []
    if match_id:
        match_rows(match_id)
        record = accounts.owner("match", canonical_match(match_id))
        if accounts.ENFORCE_AUTH and (not record or record["hidden"] or record["user_id"] != accounts.current()["id"]):
            raise HTTPException(404, "资源不存在或不可访问")
        clauses.append("match_id=?")
        params.append(canonical_match(match_id))
    if game:
        clauses.append("game_number=?")
        params.append(game)
    sql = "SELECT * FROM shots" + (" WHERE " + " AND ".join(clauses) if clauses else "")
    sql += " ORDER BY match_id,game_number,rally_number_in_game,shot_number_in_rally"
    columns = [r["name"] for r in query("PRAGMA table_info(shots)")]
    def generate():
        # Streaming bounds export memory. Recheck current ownership for each batch.
        with owned_database(personal=True) as conn:
            cursor = conn.execute(sql, params)
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(columns)
            yield "\ufeff" + output.getvalue()
            while rows := cursor.fetchmany(1000):
                user = accounts.current()
                if user:
                    with accounts.database() as identities:
                        active = identities.execute("SELECT 1 FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND u.enabled=1 AND s.csrf=? AND s.expires>?", (user["id"], user["csrf"], __import__('time').time())).fetchone()
                    if not active or not active[0]:
                        return
                still_allowed = accounts.allowed("match", personal=True)
                output.seek(0)
                output.truncate(0)
                for row in rows:
                    if still_allowed is not None and row["match_id"] not in still_allowed:
                        continue
                    writer.writerow("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in row)
                yield output.getvalue()
    from fastapi.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="my-matches.csv"', "Cache-Control": "no-store"})


app.add_middleware(accounts.LoginContextMiddleware)
app.add_middleware(security_config.SecurityHeadersMiddleware)
