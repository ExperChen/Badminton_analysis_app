"""Local identities and durable per-account resource ownership."""
import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
import security_config
from contextlib import contextmanager
from contextvars import ContextVar

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
ENFORCE_AUTH = True
principal = ContextVar("local_principal", default=None)
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
COOKIE = "yuxu_local_session"
SCOPED_COOKIE = "yuxu_tab_session"
SESSION_SECONDS = 12 * 3600
PASSWORD_WORK = threading.BoundedSemaphore(2)
DUMMY_PASSWORD = PASSWORDS.hash(secrets.token_urlsafe(32))


class LoginContextMiddleware:
    """Route a public context selector; credentials remain in path-scoped HttpOnly cookies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            matched = re.match(r"^/s/([0-9a-f]{32})(?=/|$)", scope["path"])
            if matched:
                scope["root_path"] = matched.group(0)
                scope["login_context"] = matched.group(1)
        await self.app(scope, receive, send)


def login_context(request):
    return request.scope.get("login_context", "")


def cookie_settings(request):
    context = login_context(request)
    return (SCOPED_COOKIE, f"/s/{context}/") if context else (COOKIE, "/")


def session_token(request):
    return request.cookies.get(cookie_settings(request)[0], "")


@contextmanager
def database():
    import video_tools
    folder = video_tools.MEDIA_ROOT / "accounts"
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "system.db", timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')), enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL)")
        if "context" not in {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}:
            conn.execute("ALTER TABLE sessions ADD COLUMN context TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE TABLE IF NOT EXISTS ownership (kind TEXT NOT NULL, resource_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id), hidden INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind,resource_id))")
        conn.execute("CREATE INDEX IF NOT EXISTS ownership_user ON ownership(user_id,kind,hidden)")
        conn.execute("CREATE TABLE IF NOT EXISTS match_bindings (match_id TEXT PRIMARY KEY, project_id TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS reset_tokens (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, action TEXT NOT NULL, kind TEXT, resource_id TEXT, target_user TEXT, reason TEXT, created REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS auth_limits (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires REAL NOT NULL)")
        yield conn


def initialized():
    with database() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def current():
    return principal.get()


def privileged():
    return not ENFORCE_AUTH or current() is None or current().get("role") in {"admin", "worker"}


def admin_required():
    if ENFORCE_AUTH and (not current() or current().get("role") != "admin"):
        raise HTTPException(403, "需要管理员权限")


def owner(kind, resource_id):
    with database() as conn:
        row = conn.execute("SELECT user_id,hidden FROM ownership WHERE kind=? AND resource_id=?", (kind, resource_id)).fetchone()
    return dict(row) if row else None


def visible(kind, resource_id):
    if privileged():
        return True
    record = owner(kind, resource_id)
    return bool(record and not record["hidden"] and record["user_id"] == current()["id"])


def require(kind, resource_id):
    if not visible(kind, resource_id):
        raise HTTPException(404, "资源不存在或不可访问")


def allowed(kind, personal=False):
    if privileged() and (not personal or not current()):
        return None
    with database() as conn:
        return {r[0] for r in conn.execute("SELECT resource_id FROM ownership WHERE kind=? AND user_id=? AND hidden=0", (kind, current()["id"]))}


def claim(kind, resource_id, user_id=None):
    if not ENFORCE_AUTH or (user_id is None and current() is None):
        return
    user_id = user_id or current()["id"]
    if current() and current().get("role") == "worker" and user_id == "executor":
        raise HTTPException(409, "后台任务缺少本地所有者")
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT user_id FROM ownership WHERE kind=? AND resource_id=?", (kind, resource_id)).fetchone()
        if old and old[0] != user_id:
            raise HTTPException(409, "资源归属冲突，未覆盖原所有者")
        conn.execute("INSERT OR IGNORE INTO ownership(kind,resource_id,user_id) VALUES(?,?,?)", (kind, resource_id, user_id))


def bind_match(match_id, project_id):
    if not ENFORCE_AUTH:
        return
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        record = conn.execute("SELECT user_id,hidden FROM ownership WHERE kind='project' AND resource_id=?", (project_id,)).fetchone()
        if not record:
            return
        old = conn.execute("SELECT user_id FROM ownership WHERE kind='match' AND resource_id=?", (match_id,)).fetchone()
        previous = conn.execute("SELECT project_id FROM match_bindings WHERE match_id=?", (match_id,)).fetchone()
        if (old and old[0] != record[0]) or (previous and previous[0] != project_id):
            raise HTTPException(409, "比赛归属冲突")
        conn.execute("INSERT OR IGNORE INTO ownership VALUES('match',?,?,?)", (match_id, record[0], record[1]))
        if record[1]:
            conn.execute("UPDATE ownership SET hidden=1 WHERE kind='match' AND resource_id=?", (match_id,))
        conn.execute("INSERT OR IGNORE INTO match_bindings VALUES(?,?)", (match_id, project_id))


def hide_project_matches(project_id):
    if ENFORCE_AUTH:
        with database() as conn:
            conn.execute("UPDATE ownership SET hidden=1 WHERE kind='match' AND resource_id IN (SELECT match_id FROM match_bindings WHERE project_id=?)", (project_id,))


def hide(kind, resource_id):
    if not ENFORCE_AUTH:
        return
    with database() as conn:
        conn.execute("UPDATE ownership SET hidden=1 WHERE kind=? AND resource_id=?", (kind, resource_id))


def authenticate(request):
    token = session_token(request)
    if not token:
        return None
    with database() as conn:
        row = conn.execute("SELECT u.id,u.email,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.context=? AND s.expires>? AND u.enabled=1", (hashlib.sha256(token.encode()).hexdigest(), login_context(request), time.time())).fetchone()
    return dict(row) if row else None


def rate_limit(request, purpose, identity=""):
    # Persist both source and account counters; proxy headers are never trusted here.
    source = request.client.host if request.client else "unknown"
    keys = [hashlib.sha256(f"{purpose}:ip:{source}".encode()).hexdigest()]
    if identity:
        keys.append(hashlib.sha256(f"{purpose}:account:{identity}".encode()).hexdigest())
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_limits WHERE expires<=?", (now,))
        for key in keys:
            row = conn.execute("SELECT attempts FROM auth_limits WHERE key=?", (key,)).fetchone()
            if row and row[0] >= 15:
                raise HTTPException(429, "尝试次数过多，请五分钟后重试", headers={"Retry-After": "300"})
        for key in keys:
            conn.execute("INSERT INTO auth_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1", (key, now + 300))


@contextmanager
def password_work():
    if not PASSWORD_WORK.acquire(blocking=False):
        raise HTTPException(429, "登录服务繁忙，请稍后重试", headers={"Retry-After": "3"})
    try:
        yield
    finally:
        PASSWORD_WORK.release()


def hash_password(value):
    with password_work():
        return PASSWORDS.hash(value)


def normalize(email):
    value = email.strip().casefold()
    if len(value) > 254 or value.count("@") != 1 or any(c.isspace() for c in value) or not all(value.split("@")):
        raise HTTPException(422, "请输入有效邮箱")
    return value


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class Registration(Credentials):
    model_config = ConfigDict(extra="forbid")


def registration_admin(conn):
    rows = conn.execute("SELECT id,enabled FROM users WHERE role='admin'").fetchall()
    return rows[0]["id"] if len(rows) == 1 and rows[0]["enabled"] else None


def registration_scope(request):
    if os.environ.get("YUXU_SELF_REGISTRATION", "1") != "1":
        return False
    if request.url.hostname in security_config.LOCAL_HOSTS:
        return bool(request.client and request.client.host in {"127.0.0.1", "::1"})
    return (os.environ.get("YUXU_REQUIRE_VPN_IDENTITY") == "1"
            and f"{request.url.scheme}://{request.url.netloc}" == security_config.public_origin()
            and request.url.scheme == "https" and bool(request.headers.get("tailscale-user-login")))


def registration_budget(request):
    # Changing email or reconnecting cannot evade the durable per-VPN limit.
    identity = request.headers.get("tailscale-user-login") if request.url.hostname not in security_config.LOCAL_HOSTS else request.client.host
    key = hashlib.sha256(f"self-register-day:{identity}".encode()).hexdigest()
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT attempts,expires FROM auth_limits WHERE key=?", (key,)).fetchone()
        if row and row["expires"] > now and row["attempts"] >= 5:
            raise HTTPException(429, "当前网络身份的注册次数已达每日上限，请稍后重试", headers={"Retry-After": str(max(1, int(row["expires"] - now)))})
        if not row or row["expires"] <= now:
            conn.execute("INSERT OR REPLACE INTO auth_limits VALUES(?,1,?)", (key, now + 86400))
        else:
            conn.execute("UPDATE auth_limits SET attempts=attempts+1 WHERE key=?", (key,))


def login_response(user, request):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with database() as conn:
        conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        conn.execute("INSERT INTO sessions(digest,user_id,csrf,expires,context) VALUES(?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, time.time() + SESSION_SECONDS, login_context(request)))
    response = JSONResponse({"id": user["id"], "email": user["email"], "role": user["role"], "csrf": csrf}, headers={"Cache-Control": "no-store"})
    name, path = cookie_settings(request)
    response.set_cookie(name, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", path=path,
                        secure=request.url.scheme == "https")
    return response


@router.get("/api/auth/status")
def auth_status(request: Request):
    with database() as conn:
        ready = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        enabled = registration_scope(request) and registration_admin(conn) is not None
    return {"initialized": ready, "registration_open": enabled}


@router.post("/api/auth/setup")
def setup(body: Credentials, request: Request):
    rate_limit(request, "setup")
    if initialized():
        raise HTTPException(409, "管理员已初始化，请登录")
    if not request.client or request.client.host not in {"127.0.0.1", "::1"} or request.url.hostname not in security_config.LOCAL_HOSTS:
        raise HTTPException(403, "首次管理员设置只能在本机完成")
    email, hashed = normalize(body.email), hash_password(body.password)
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "管理员已初始化，请登录")
        user = {"id": uuid.uuid4().hex, "email": email, "role": "admin"}
        conn.execute("INSERT INTO users VALUES(?,?,?,?,1,?)", (user["id"], email, hashed, "admin", time.time()))
    return login_response(user, request)


@router.post("/api/auth/register")
def register(body: Registration, request: Request):
    if not registration_scope(request):
        raise HTTPException(403, "自助注册仅开放给已授权的私有网络，请连接 Tailscale 后访问 HTTPS 网站")
    if current():
        raise HTTPException(409, "请在新的登录窗口注册，当前账号不会被切换")
    email = normalize(body.email)
    rate_limit(request, "register", email)
    registration_budget(request)
    with database() as conn:
        if not registration_admin(conn):
            raise HTTPException(403, "管理员尚未就绪，暂不能注册")
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "该邮箱已注册，请直接登录或联系管理员重置密码")
    hashed = hash_password(body.password)
    user = {"id": uuid.uuid4().hex, "email": email, "role": "user"}
    try:
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            admin_id = registration_admin(conn)
            if not admin_id:
                raise HTTPException(403, "管理员尚未就绪，暂不能注册")
            conn.execute("INSERT INTO users VALUES(?,?,?,?,1,?)", (user["id"], email, hashed, "user", time.time()))
            conn.execute("INSERT INTO audit(actor,action,target_user,reason,created) VALUES(?,?,?,?,?)",
                         (user["id"], "self_register", user["id"], "Managed by existing administrator: " + admin_id, time.time()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "该邮箱已注册，请直接登录或联系管理员重置密码") from None
    return login_response(user, request)


@router.post("/api/auth/login")
def login(body: Credentials, request: Request):
    email = normalize(body.email)
    rate_limit(request, "login", email)
    with database() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    try:
        with password_work():
            PASSWORDS.verify(row["password_hash"] if row else DUMMY_PASSWORD, body.password)
        if not row or not row["enabled"]:
            raise HTTPException(401, "邮箱或密码不正确")
    except (VerificationError, InvalidHashError) as exc:
        raise HTTPException(401, "邮箱或密码不正确") from exc
    return login_response(dict(row), request)


@router.get("/api/me")
def me():
    return current()


@router.get("/api/uploads/limits")
def my_upload_limits():
    import upload_limits
    if not current():
        raise HTTPException(401, "请先登录")
    return upload_limits.summary(current()["id"])


@router.get("/api/admin/upload-limits")
def account_upload_limits():
    admin_required()
    import upload_limits
    with database() as conn:
        identities = [dict(r) for r in conn.execute("SELECT id,email FROM users ORDER BY created")]
    return [{**identity, **upload_limits.summary(identity["id"])} for identity in identities]


@router.post("/api/auth/logout")
def logout(request: Request):
    with database() as conn:
        conn.execute("DELETE FROM sessions WHERE digest=? AND context=?", (hashlib.sha256(session_token(request).encode()).hexdigest(), login_context(request)))
    response = JSONResponse({"ok": True})
    name, path = cookie_settings(request)
    response.delete_cookie(name, path=path)
    return response


@router.get("/api/admin/users")
def users():
    admin_required()
    with database() as conn:
        return [dict(r) for r in conn.execute("SELECT id,email,role,enabled,created FROM users ORDER BY created")]


class NewUser(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router.post("/api/admin/users", status_code=201)
def create_user(body: NewUser, request: Request):
    admin_required()
    email = normalize(body.email)
    rate_limit(request, "create_user")
    with database() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "该账号已存在，请签发密码重置码")
    user_id, activation = uuid.uuid4().hex, secrets.token_urlsafe(32)
    hashed = hash_password(secrets.token_urlsafe(48))
    try:
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO users VALUES(?,?,?,?,1,?)", (user_id, email, hashed, "user", time.time()))
            conn.execute("INSERT INTO reset_tokens VALUES(?,?,?)", (hashlib.sha256(activation.encode()).hexdigest(), user_id, time.time() + 900))
            conn.execute("INSERT INTO audit(actor,action,target_user,created) VALUES(?,?,?,?)", (current()["id"], "create_user", user_id, time.time()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "该账号已存在，请签发密码重置码") from None
    return {"id": user_id, "email": email, "role": "user", "token": activation, "expires_in": 900}


@router.get("/api/admin/uploads")
def uploads_by_account():
    """Inventory logical uploads by their recorded owner, including legacy orphans."""
    admin_required()
    import json
    from pathlib import Path
    import tracker
    import video_tools

    with database() as conn:
        groups = [dict(row) for row in conn.execute(
            "SELECT id,email,role,enabled FROM users ORDER BY created,id")]
        ownership = {(row["kind"], row["resource_id"]): dict(row) for row in conn.execute(
            "SELECT kind,resource_id,user_id,hidden FROM ownership WHERE kind IN ('session','project')")}
    by_user = {group["id"]: group for group in groups}
    for group in groups:
        group["videos"] = []
    unassigned_videos = []
    projects_by_session = {}
    with tracker.database() as conn:
        rows = conn.execute("SELECT id,payload FROM projects ORDER BY updated_at DESC,id").fetchall()
    for row in rows:
        project_owner = ownership.get(("project", row["id"]))
        if project_owner and project_owner["hidden"]:
            continue
        project = json.loads(row["payload"])
        projects_by_session.setdefault(project["session_id"], []).append({
            "id": row["id"], "title": project.get("title", row["id"]),
        })

    # A shared physical file is still one logical upload per owning account.
    # Never infer an owner from a project title, player name or filesystem path.
    for sid, record in video_tools.registry().items():
        session_owner = ownership.get(("session", sid))
        if session_owner and session_owner["hidden"]:
            continue
        try:
            file_available = bool(record.get("path")) and Path(record["path"]).is_file()
        except OSError:
            file_available = False
        video = {
            "session_id": sid, "title": record.get("title") or record.get("filename") or sid,
            "filename": record.get("filename", ""), "duration": record.get("duration"),
            "uploaded_at": record.get("created_at"), "updated_at": record.get("updated_at"),
            "file_available": file_available, "projects": projects_by_session.get(sid, []),
        }
        group = by_user.get(session_owner["user_id"]) if session_owner else None
        (group["videos"] if group else unassigned_videos).append(video)
    for videos in [group["videos"] for group in groups] + [unassigned_videos]:
        videos.sort(key=lambda video: (video["uploaded_at"] or video["updated_at"] or "", video["session_id"]), reverse=True)
    for group in groups:
        group["video_count"] = len(group["videos"])
    return {
        "accounts": groups, "unassigned": {"video_count": len(unassigned_videos), "videos": unassigned_videos},
        "total_videos": sum(group["video_count"] for group in groups) + len(unassigned_videos),
    }


class UserState(BaseModel):
    enabled: bool


@router.post("/api/admin/users/{user_id}/state")
def user_state(user_id: str, body: UserState):
    admin_required()
    if user_id == current()["id"]:
        raise HTTPException(409, "不能停用当前管理员")
    with database() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND role='user'", (user_id,)).fetchone():
            raise HTTPException(404, "用户不存在")
        conn.execute("UPDATE users SET enabled=? WHERE id=?", (int(body.enabled), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        conn.execute("INSERT INTO audit(actor,action,target_user,created) VALUES(?,?,?,?)", (current()["id"], "enable" if body.enabled else "suspend", user_id, time.time()))
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/reset-token")
def reset_token(user_id: str):
    admin_required()
    token = secrets.token_urlsafe(32)
    with database() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "用户不存在")
        conn.execute("DELETE FROM reset_tokens WHERE user_id=?", (user_id,))
        conn.execute("INSERT INTO reset_tokens VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 900))
        conn.execute("INSERT INTO audit(actor,action,target_user,created) VALUES(?,?,?,?)", (current()["id"], "reset_token", user_id, time.time()))
    return {"token": token, "expires_in": 900}


class ResetPassword(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=128)


@router.post("/api/auth/password-reset/complete")
def reset_password(body: ResetPassword, request: Request):
    rate_limit(request, "reset")
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    with database() as conn:
        row = conn.execute("SELECT t.user_id FROM reset_tokens t JOIN users u ON u.id=t.user_id WHERE t.digest=? AND t.expires>? AND u.enabled=1", (digest, time.time())).fetchone()
        if not row:
            raise HTTPException(400, "重置码无效或已过期")
    hashed = hash_password(body.password)
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT t.user_id FROM reset_tokens t JOIN users u ON u.id=t.user_id WHERE t.digest=? AND t.expires>? AND u.enabled=1", (digest, time.time())).fetchone()
        if not row:
            raise HTTPException(400, "重置码无效或已过期")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, row[0]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row[0],))
        conn.execute("DELETE FROM reset_tokens WHERE user_id=?", (row[0],))
    return {"ok": True}


class AssignOwner(BaseModel):
    user_id: str
    reason: str = Field(min_length=3, max_length=500)


@router.post("/api/admin/resources/{kind}/{resource_id}/assign")
def assign_owner(kind: str, resource_id: str, body: AssignOwner):
    admin_required()
    if kind not in {"match", "session", "project"}:
        raise HTTPException(422, "不支持此资源类型")
    # Only existing, unassigned resources may be claimed. Transfers need a separate workflow.
    from app import DB_PATH
    import video_tools
    import tracker
    if kind == "match":
        with sqlite3.connect(DB_PATH) as conn:
            exists = conn.execute("SELECT 1 FROM shots WHERE match_id=?", (resource_id,)).fetchone()
    elif kind == "session":
        exists = resource_id in video_tools.registry()
    else:
        exists = tracker.project(resource_id)
    if not exists:
        raise HTTPException(404, "资源不存在")
    if owner(kind, resource_id):
        raise HTTPException(409, "资源已有归属；本版只支持认领未归属资源")
    linked = [(kind, resource_id)]
    if kind == "project":
        saved = tracker.project(resource_id)
        linked.append(("session", saved["session_id"]))
        linked.extend(("snapshot", p["snapshot_id"]) for p in saved.get("players", []))
        import local_data
        linked.extend(("match", mid) for mid, pid in local_data.project_matches(DB_PATH).items()
                      if pid == resource_id)
    elif kind == "match":
        import local_data
        session = local_data.aliases(DB_PATH).get(resource_id)
        if session:
            linked.append(("session", session))
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (body.user_id,)).fetchone():
            raise HTTPException(404, "用户不存在或已停用")
        for resource_kind, rid in linked:
            old = conn.execute("SELECT user_id,hidden FROM ownership WHERE kind=? AND resource_id=?", (resource_kind, rid)).fetchone()
            if old and (old[0] != body.user_id or old[1]):
                raise HTTPException(409, "关联资源归属冲突，未认领任何数据")
            conn.execute("INSERT OR IGNORE INTO ownership(kind,resource_id,user_id) VALUES(?,?,?)", (resource_kind, rid, body.user_id))
            if kind == "project" and resource_kind == "match":
                binding = conn.execute("SELECT project_id FROM match_bindings WHERE match_id=?", (rid,)).fetchone()
                if binding and binding[0] != resource_id:
                    raise HTTPException(409, "比赛已有其他项目关联，未认领任何数据")
                conn.execute("INSERT OR IGNORE INTO match_bindings VALUES(?,?)", (rid, resource_id))
        conn.execute("INSERT INTO audit(actor,action,kind,resource_id,target_user,reason,created) VALUES(?,?,?,?,?,?,?)", (current()["id"], "assign", kind, resource_id, body.user_id, body.reason, time.time()))
    return {"ok": True, "assigned": len(linked)}


@router.get("/api/admin/unassigned")
def unassigned():
    admin_required()
    from app import DB_PATH
    import tracker
    import video_tools
    with database() as conn:
        owned = {(r[0], r[1]) for r in conn.execute("SELECT kind,resource_id FROM ownership")}
    with sqlite3.connect(DB_PATH) as conn:
        matches = [{"kind": "match", "id": r[0], "title": r[1]} for r in conn.execute("SELECT match_id,MAX(match_title) FROM shots GROUP BY match_id") if ("match", r[0]) not in owned]
    projects = [{"kind": "project", "id": p["id"], "title": p.get("title", p["id"])} for p in tracker.projects() if ("project", p["id"]) not in owned]
    sessions = [{"kind": "session", "id": sid, "title": v["title"]} for sid, v in video_tools.registry().items() if ("session", sid) not in owned]
    return projects + matches + sessions


@router.get("/api/admin/audit")
def audit():
    admin_required()
    with database() as conn:
        return [dict(r) for r in conn.execute("SELECT actor,action,kind,resource_id,target_user,reason,created FROM audit ORDER BY id DESC LIMIT 100")]
