"""Local tracker projects, frame extraction and player snapshots."""
import hashlib
import json
import sqlite3
import uuid
import accounts
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

import video_tools as vt

router = APIRouter(prefix="/api/tracker")


@contextmanager
def database():
    root = vt.MEDIA_ROOT / "tracker"
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "projects.db", timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS deleted_projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL, migration_payload TEXT, deleted_at TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS saved_player_names (
            owner_id TEXT NOT NULL, normalized_name TEXT NOT NULL, name TEXT NOT NULL,
            handedness TEXT NOT NULL CHECK(handedness IN ('right','left')),
            is_you INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            PRIMARY KEY(owner_id,normalized_name))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS video_fingerprints (
            path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL, computed_at TEXT NOT NULL)""")
        yield conn


def _player_owner_id():
    user = accounts.current()
    if accounts.ENFORCE_AUTH:
        return user["id"] if user and user.get("role") in {"admin", "user"} else None
    return "local"


def _remember_players(conn, players, owner_id):
    if not owner_id:
        return
    updated_at = vt.now()
    for player in players:
        name = " ".join(player.name.split())
        normalized = name.casefold()
        conn.execute("""INSERT INTO saved_player_names
            (owner_id,normalized_name,name,handedness,is_you,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(owner_id,normalized_name) DO UPDATE SET
            name=excluded.name, handedness=excluded.handedness,
            is_you=MAX(saved_player_names.is_you,excluded.is_you), updated_at=excluded.updated_at""",
            (owner_id, normalized, name, player.handedness, int(player.is_you), updated_at))


def saved_player_options():
    """Return this local account's durable names plus names from its legacy projects."""
    owner_id = _player_owner_id()
    if not owner_id:
        return []
    result = {}
    with database() as conn:
        for row in conn.execute("SELECT name,handedness,is_you FROM saved_player_names WHERE owner_id=? ORDER BY updated_at DESC", (owner_id,)):
            result[row["name"].casefold()] = dict(row)
        allowed = accounts.allowed("project", personal=True)
        for row in conn.execute("SELECT id,payload FROM projects ORDER BY updated_at DESC"):
            if allowed is not None and row["id"] not in allowed:
                continue
            for player in json.loads(row["payload"]).get("players", []):
                name = " ".join(str(player.get("name", "")).split())
                hand = player.get("handedness")
                if not name or hand not in {"right", "left"}:
                    continue
                key = name.casefold()
                if key not in result:
                    result[key] = {"name": name, "handedness": hand,
                                   "is_you": int(bool(player.get("is_you")))}
                elif player.get("is_you"):
                    result[key]["is_you"] = 1
    return sorted(result.values(), key=lambda item: item["name"].casefold())


class Box(BaseModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(ge=.005, le=1, allow_inf_nan=False)
    height: float = Field(ge=.005, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("选框超出视频画面")
        return self


class SnapshotInput(BaseModel):
    session_id: str
    time: float = Field(ge=0, allow_inf_nan=False)
    rotation: int = 0
    box: Box


def video(session_id, time, rotation):
    try:
        record, path = vt.source(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if rotation not in (0, 90, 180, 270):
        raise HTTPException(422, "旋转角度无效")
    if time >= record["duration"]:
        raise HTTPException(422, "选帧时间超出视频长度")
    return record, path


def frame_bytes(path, time, rotation, box=None):
    # FFmpeg autorotates metadata first; manual rotation is shared by preview/crop.
    transforms = {0: [], 90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}[rotation]
    transforms += ["scale=w='min(1280,iw)':h=-2", "setsar=1"]
    if box:
        transforms += [f"crop=iw*{box.width}:ih*{box.height}:iw*{box.x}:ih*{box.y}"]
    import subprocess
    result = subprocess.run([vt.binary("ffmpeg"), "-v", "error", "-nostdin",
        "-protocol_whitelist", "file,pipe", "-threads", "1", "-filter_threads", "1", "-ss", str(time), "-i", str(path),
        "-frames:v", "1", "-vf", ",".join(transforms), "-threads", "1",
        "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"], capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if vt.os.name == "nt" else 0)
    if result.returncode or not result.stdout:
        raise HTTPException(422, "无法读取这一帧，请选择稍早的时间后重试。")
    return result.stdout


@router.get("/frame/{session_id}")
def frame(session_id: str, time: float = Query(0, ge=0, allow_inf_nan=False), rotation: int = 0):
    _, path = video(session_id, time, rotation)
    return Response(frame_bytes(path, time, rotation), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/snapshots", status_code=201)
def snapshot(body: SnapshotInput):
    record, path = video(body.session_id, body.time, body.rotation)
    content = frame_bytes(path, body.time, body.rotation, body.box)
    sid = uuid.uuid4().hex
    accounts.claim("snapshot", sid)
    payload = {**body.model_dump(), "id": sid, "video_version": record["updated_at"],
               "url": f"/api/tracker/snapshots/{sid}"}
    with database() as conn:
        (vt.MEDIA_ROOT / "tracker" / (sid + ".jpg")).write_bytes(content)
        conn.execute("INSERT INTO snapshots VALUES (?,?)", (sid, json.dumps(payload)))
    return payload


@router.get("/snapshots/{sid}")
def snapshot_image(sid: str):
    accounts.require("snapshot", sid)
    with database() as conn:
        row = conn.execute("SELECT payload FROM snapshots WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "截图不存在")
    return FileResponse(vt.MEDIA_ROOT / "tracker" / (json.loads(row[0])["id"] + ".jpg"))


class Player(BaseModel):
    slot: str
    name: str = Field(min_length=1, max_length=80)
    handedness: str = Field(pattern="^(right|left)$")
    is_you: bool = False
    snapshot_id: str


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    first_server: str = Field(pattern="^(near|far)$")
    a_start: int | None = Field(None, ge=0, le=999)
    b_start: int | None = Field(None, ge=0, le=999)
    a_end: int | None = Field(None, ge=0, le=999)
    b_end: int | None = Field(None, ge=0, le=999)


class Project(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    match_date: date | None = None
    session_id: str
    game_type: str = Field(pattern="^(singles|doubles)$")
    playing: bool
    players: list[Player] = Field(min_length=2, max_length=4)
    segments: list[Segment] = Field(default_factory=list, max_length=100)


def validate_project(body, conn):
    record, _ = video(body.session_id, 0, 0)
    slots = {"A1", "B1"} if body.game_type == "singles" else {"A1", "A2", "B1", "B2"}
    if len(body.players) != len(slots) or {p.slot for p in body.players} != slots:
        raise HTTPException(422, "请完成所有球员设置")
    if sum(p.is_you for p in body.players) != int(body.playing):
        raise HTTPException(422, "参赛时请选择且只选择一个本人位置")
    if not body.title.strip() or any(not p.name.strip() for p in body.players):
        raise HTTPException(422, "名称不能为空")
    snapshots = {}
    for p in body.players:
        accounts.require("snapshot", p.snapshot_id)
        row = conn.execute("SELECT payload FROM snapshots WHERE id=?", (p.snapshot_id,)).fetchone()
        if not row:
            raise HTTPException(422, f"{p.slot} 截图未保存，请重新框选")
        snap = json.loads(row[0])
        if snap["session_id"] != body.session_id or snap["video_version"] != record["updated_at"]:
            raise HTTPException(422, f"{p.slot} 截图对应的视频已更换，请重新框选")
        if not (vt.MEDIA_ROOT / "tracker" / (snap["id"] + ".jpg")).is_file():
            raise HTTPException(422, f"{p.slot} 截图文件丢失，请重新框选")
        snapshots[p.slot] = snap
    previous = 0
    for s in sorted(body.segments, key=lambda x: x.start):
        if s.end <= s.start or s.end > record["duration"] or s.start < previous:
            raise HTTPException(422, "分段需在视频时长内、结束晚于开始且互不重叠")
        for start, end in ((s.a_start, s.a_end), (s.b_start, s.b_end)):
            if start is not None and end is not None and end < start:
                raise HTTPException(422, "片段结束比分不能小于开始比分；新一局请另建片段")
        previous = s.end
    return snapshots


def save_project(body, pid=None):
    creating = pid is None
    if pid:
        accounts.require("project", pid)
    with database() as conn:
        previous = conn.execute("SELECT payload FROM projects WHERE id=?", (pid,)).fetchone() if pid else None
        if pid and not previous:
            raise HTTPException(404, "项目不存在")
        snaps = validate_project(body, conn)
        pid = pid or uuid.uuid4().hex
        if creating:
            accounts.claim("project", pid)
        # Administrative edits belong to the project's account, not the editor.
        ownership = accounts.owner("project", pid) if accounts.ENFORCE_AUTH else None
        owner_id = ownership["user_id"] if ownership else (None if accounts.ENFORCE_AUTH else "local")
        _remember_players(conn, body.players, owner_id)
        payload = {**body.model_dump(mode="json"), "id": pid, "snapshots": snaps,
                   "status": "ready"}
        if previous:
            saved = json.loads(previous["payload"])
            # Unchanged forms retain their stable revision.
            if {key: value for key, value in saved.items() if key != "updated_at"} == payload:
                return saved
        payload["updated_at"] = vt.now()
        conn.execute("INSERT OR REPLACE INTO projects VALUES (?,?,?)", (pid, json.dumps(payload), payload["updated_at"]))
    return payload


@router.post("/projects", status_code=201)
def create_project(body: Project):
    return save_project(body)


@router.put("/projects/{pid}")
def update_project(pid: str, body: Project):
    return save_project(body, pid)


@router.get("/projects")
def projects():
    from app import DB_PATH
    import local_data
    matches = local_data.project_matches(DB_PATH)
    linked = {pid: mid for mid, pid in matches.items()}
    with database() as conn:
        rows = conn.execute("SELECT id,payload FROM projects ORDER BY updated_at DESC").fetchall()
    return [{**json.loads(row["payload"]), "analysis_match_id": linked.get(row["id"])}
            for row in rows if accounts.visible("project", row["id"])]


@router.get("/projects/{pid}")
def project(pid: str):
    accounts.require("project", pid)
    with database() as conn:
        row = conn.execute("SELECT payload FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return json.loads(row[0])


@router.delete("/projects/{pid}")
def delete_project(pid: str):
    """Archive local project settings without removing original videos or shot data."""
    accounts.require("project", pid)
    with database() as conn:
        row = conn.execute("SELECT payload FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        saved = json.loads(row[0])
        deleted_at = vt.now()
        conn.execute("INSERT OR REPLACE INTO deleted_projects(id,payload,deleted_at) VALUES (?,?,?)",
                     (pid, row[0], deleted_at))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    accounts.hide("project", pid)
    accounts.hide_project_matches(pid)
    return {"id": pid, "title": saved["title"], "deleted_at": deleted_at,
            "recoverable": True, "video_retained": True}
