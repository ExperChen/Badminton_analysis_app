"""Local migration queue and authenticated extension bridge."""
import hashlib
import csv
import io
import json
import logging
import os
import secrets
import tempfile
import time
import uuid
from contextlib import contextmanager
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from tracker import (database, frame_bytes, save_migration, association_for,
                     project as load_project, MigrationLink, Project as TrackerProject, validate_project)
import video_tools as vt
import full_csv_sync
import accounts
import cost_estimation
import token_wallet

router = APIRouter(prefix="/api/bridge")
SESSION_PREFLIGHT_ERROR = "GGAB 登录会话不可用；请在同一浏览器登录并刷新已有 GGAB 页面"
SOURCE_CHANGED_ERROR = "本地项目或原视频已变化，请重新核对"
CONFIRM_EXTENSION_VERSION = "1.5.0"
VIDEO_CHECK_EXTENSION_VERSION = "1.4.3"
VIDEO_ONLY_STAGES = frozenset({"lookup_video", "init_video", "start_upload", "upload_video",
                                "finalize_video", "wait_video"})


@contextmanager
def db():
    with database() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS bridge_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS bridge_jobs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, remote_id TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, lease_until REAL,
            lease TEXT, error TEXT, observed_status TEXT, finished REAL,
            kind TEXT NOT NULL DEFAULT 'sync_status', diagnostic TEXT)""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(bridge_jobs)")}
        if "kind" not in columns:
            conn.execute("ALTER TABLE bridge_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'sync_status'")
        if "diagnostic" not in columns:
            conn.execute("ALTER TABLE bridge_jobs ADD COLUMN diagnostic TEXT")
        yield conn


def authorize(request):
    raw = request.headers.get("authorization", "")
    if not raw.startswith("Bearer "):
        raise HTTPException(401, "扩展尚未连接本地服务")
    digest = hashlib.sha256(raw[7:].encode()).hexdigest()
    with db() as conn:
        row = conn.execute("SELECT value FROM bridge_settings WHERE key='token'").fetchone()
    if not row or not secrets.compare_digest(row[0], digest):
        raise HTTPException(401, "扩展配对已失效，请重新连接")


@router.post("/pair")
def pair():
    accounts.admin_required()
    # Protected by the ordinary same-origin local-mutation middleware.
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO bridge_settings VALUES ('token', ?)",
                     (hashlib.sha256(token.encode()).hexdigest(),))
    from fastapi.responses import JSONResponse
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})


class SharedSource(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)


@router.post("/worker/source")
def identify_source(body: SharedSource, request: Request):
    authorize(request)
    try:
        subject = str(uuid.UUID(body.user_id))
    except ValueError as exc:
        raise HTTPException(422, "GGAB 身份标识无效") from exc
    with accounts.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key='ggab_subject'").fetchone()
        if row and row[0] != subject:
            raise HTTPException(409, "共享 GGAB 身份已变化，禁止切换账号后继续原任务")
        conn.execute("INSERT OR IGNORE INTO settings VALUES('ggab_subject',?)", (subject,))
    return {"ok": True, "user_id": subject}


class TokenBalance(BaseModel):
    total: int | None = Field(None, ge=0, le=token_wallet.MAX_TOKENS, strict=True)
    error: str | None = Field(None, min_length=1, max_length=500)
    activity: list[dict] = Field(default_factory=list, max_length=500)
    activity_error: str | None = Field(None, min_length=1, max_length=500)


@router.post("/worker/token-balance")
def observe_token_balance(body: TokenBalance, request: Request):
    authorize(request)
    if (body.total is None) == (body.error is None):
        raise HTTPException(422, "必须提供余额或读取失败原因")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        token_wallet.remember_balance(conn, body.total, body.error)
        if body.total is not None:
            token_wallet.remember_activity(conn, body.activity)
            token_wallet.remember_activity_sync(conn, body.activity_error)
            token_wallet.settle_ready(conn)
    return {"ok": True}


@router.get("/status")
def status():
    from app import DB_PATH
    records = vt.registry()
    with db() as conn:
        cost_estimation.backfill(conn)
        heartbeat = conn.execute("SELECT value FROM bridge_settings WHERE key='heartbeat'").fetchone()
        extension_version = conn.execute("SELECT value FROM bridge_settings WHERE key='extension_version'").fetchone()
        workflows = [dict(r) for r in conn.execute(
            "SELECT project_id,stage,payload,updated_at FROM migration_workflows ORDER BY updated_at DESC")]
        jobs = [dict(r) for r in conn.execute(
            "SELECT id,project_id,remote_id,state,kind,error,observed_status,diagnostic,created,finished FROM bridge_jobs ORDER BY created DESC LIMIT 30")]
        links = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT project_id,payload FROM migration_links")}
        video_checks = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT v.project_id,v.payload FROM video_checks v JOIN projects p ON p.id=v.project_id")}
        predictions = {}
        for row in conn.execute("SELECT id,payload FROM projects").fetchall():
            if accounts.visible("project", row["id"]):
                saved = json.loads(row["payload"])
                predictions[row["id"]] = cost_estimation.summary(conn, saved, records.get(saved.get("session_id")), links.get(row["id"]))
        for pid, checked in list(video_checks.items()):
            try:
                if video_target(conn, pid)["video_id"] != checked["video_id"]:
                    video_checks.pop(pid)
            except HTTPException:
                video_checks.pop(pid)
    for workflow in workflows:
        if not accounts.visible("project", workflow["project_id"]):
            continue
        data = json.loads(workflow.pop("payload"))
        workflow["remote_project_id"] = data.get("remote_project_id")
        workflow["cost_prediction"] = predictions.get(workflow["project_id"])
        workflow["source_size"] = data.get("source_size")
        workflow["upload_offset"] = data.get("upload_offset", 0)
        workflow["video_filename"] = (data.get("source_filename") or data.get("remote_evidence", {}).get("filename") or
                                      (Path(data["source_path"]).name if data.get("source_path") else None))
        workflow["source"] = data.get("source")
        workflow["estimated_cost"] = data.get("estimated_cost")
        workflow["balance_before"] = data.get("balance_before")
        workflow["estimate_changed_from"] = data.get("estimate_changed_from") if workflow["stage"] == "await_confirmation" else None
        linked = links.get(workflow["project_id"], {})
        workflow["confirmation_ready"] = workflow["stage"] == "await_confirmation" and confirmation_matches(data, linked)
        if video_checks.get(workflow["project_id"], {}).get("status") in {"missing", "failed", "mismatch"}:
            workflow["confirmation_ready"] = False
        if (workflow["balance_before"] is None and linked.get("association", {}).get("verified") and
                linked.get("remote_project_id") == data.get("remote_project_id")):
            workflow["balance_before"] = linked.get("balance_before")
        workflow["error"] = data.get("error")
        if workflow["error"]:
            logging.getLogger("badminton.processing").warning(
                "project=%s stage=%s error=%s", workflow["project_id"], workflow["stage"],
                str(workflow["error"]).replace("\n", " ")[:1000])
        estimate = workflow["estimated_cost"]
        with db() as quota_conn:
            workflow["quota"] = token_wallet.project_status(quota_conn, workflow["project_id"], estimate)
        if workflow["quota"] and (workflow["quota"].get("needs_recharge") or workflow["quota"].get("requires_assignment")):
            workflow["confirmation_ready"] = False
        if not accounts.privileged():
            workflow["balance_before"] = None
            workflow.pop("cost_prediction", None)
            workflow.pop("source", None)
            workflow["error"] = "处理暂未完成，请联系管理员核查" if workflow["error"] else None
        workflow["exported_csv"] = data.get("exported_csv")
        workflow["required_extension_version"] = required_version(workflow["stage"], data)
    for job in jobs:
        job["diagnostic"] = json.loads(job["diagnostic"]) if job["diagnostic"] else None
    at = float(heartbeat[0]) if heartbeat else None
    workflows = [w for w in workflows if accounts.visible("project", w["project_id"])]
    jobs = [j for j in jobs if accounts.visible("project", j["project_id"])]
    video_checks = {pid: value for pid, value in video_checks.items() if accounts.visible("project", pid)}
    full = full_csv_sync.status(DB_PATH)
    if not accounts.privileged():
        from app import query
        counts = query("SELECT COUNT(*) AS rows,COUNT(DISTINCT match_id) AS matches FROM shots")[0]
        full = {"state": full.get("state"), "revision": full.get("revision"), "imported_at": full.get("imported_at"),
                "sha256": bool(full.get("sha256")), **counts, "error": "后台同步未完成" if full.get("error") else None}
        jobs = []
        video_checks = {pid: {"status": check["status"]} for pid, check in video_checks.items()}
        for workflow in workflows:
            workflow.pop("exported_csv", None)
    return {"connected": at is not None and time.time() - at < 150,
            "last_seen": at, "extension_version": extension_version[0] if extension_version else None,
            "capabilities": ["inspect_remote", "sync_status", "migrate", "export_csv", "full_csv_sync", "check_video"],
            "video_checks": video_checks,
            "full_export": full,
            "is_admin": accounts.privileged(), "source_verified": bool(accounts.source_subject()),
            "jobs": jobs, "workflows": workflows}


class ClaimRequest(BaseModel):
    extension_version: str | None = Field(None, max_length=30)


def video_target(conn, pid):
    link_row = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (pid,)).fetchone()
    workflow_row = conn.execute("SELECT payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
    link = json.loads(link_row[0]) if link_row else {}
    data = json.loads(workflow_row[0]) if workflow_row else {}
    evidence = link.get("association", {}).get("remote_video", {}) if link.get("association", {}).get("verified") else {}
    video_id = evidence.get("remote_video_id") or data.get("video_id")
    if not video_id:
        raise HTTPException(409, "尚无已登记的 GGAB 视频 ID，不能按标题判断上传完成")
    if evidence.get("remote_video_id") and data.get("video_id") and evidence["remote_video_id"] != data["video_id"]:
        raise HTTPException(409, "远端视频 ID 冲突，请先核对关联")
    # The upload's registered object is the original, not a later playback
    # derivative. Only bind its path when the workflow names this exact ID.
    storage_path = data.get("storage_path") if data.get("video_id") == video_id else None
    return {"video_id": video_id, "filename": evidence.get("filename") or data.get("source_filename"),
            "size_bytes": evidence.get("size_bytes") or data.get("source_size"),
            "duration": evidence.get("duration") or data.get("remote_evidence", {}).get("duration"),
            "storage_path": storage_path}


@router.post("/projects/{pid}/check-video")
def check_video(pid: str):
    load_project(pid)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        video_target(conn, pid)
        pending = conn.execute("SELECT id FROM bridge_jobs WHERE project_id=? AND kind='check_video' AND state IN ('queued','running')", (pid,)).fetchone()
        if pending:
            return {"queued": True, "job_id": pending[0]}
        linked = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (pid,)).fetchone()
        remote_id = json.loads(linked[0])["remote_project_id"] if linked else ""
        job_id = uuid.uuid4().hex
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,kind) VALUES (?,?,?,'queued',?,'check_video')",
                     (job_id, pid, remote_id, time.time()))
    return {"queued": True, "job_id": job_id}


def extension_supports_version(current, required):
    if not required:
        return True
    try:
        return tuple(int(part) for part in current.split(".")) >= tuple(int(part) for part in required.split("."))
    except (AttributeError, ValueError):
        return False


def required_version(stage, data):
    required = data.get("required_extension_version")
    if stage in {"await_confirmation", "confirm_tracking"} and extension_supports_version(
            CONFIRM_EXTENSION_VERSION, required):
        return CONFIRM_EXTENSION_VERSION
    return required


def matching_source(data, project):
    """Check the original file before any revision is accepted or bytes are served."""
    try:
        record, path = vt.source(project["session_id"])
        stat = path.stat()
    except (KeyError, ValueError, OSError):
        return None
    if (str(path.resolve()) != data.get("source_path") or stat.st_size != data.get("source_size") or
            stat.st_mtime_ns != data.get("source_mtime_ns")):
        return None
    return record, path, stat


def accept_project_revision(conn, pid, stage, data, project):
    """Use current setup while only the original video has reached GGAB."""
    revision = project.get("updated_at")
    if not isinstance(revision, str) or not revision:
        return False
    if revision == data.get("project_updated_at"):
        return True
    if (stage not in VIDEO_ONLY_STAGES or data.get("snapshot_paths") or
            data.get("remote_project_id") or
            conn.execute("SELECT 1 FROM migration_links WHERE project_id=?", (pid,)).fetchone()):
        return False
    data["project_updated_at"] = revision
    data.pop("error", None)
    conn.execute("UPDATE migration_workflows SET payload=?,updated_at=? WHERE project_id=?",
                 (json.dumps(data), vt.now(), pid))
    return True


def resume_blocked_video_upload(pid):
    """Explicitly resume a pre-setup upload blocked by a local project save.

    This does not create a new GGAB video/project. The worker HEADs the existing
    TUS URL and resumes from the server-confirmed offset on its next claim.
    """
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
        project_row = conn.execute("SELECT payload FROM projects WHERE id=?", (pid,)).fetchone()
        if not row or not project_row or row["stage"] != "blocked":
            raise ValueError("项目并非可恢复的暂停上传任务")
        data, project = json.loads(row["payload"]), json.loads(project_row["payload"])
        offset, size = data.get("upload_offset"), data.get("source_size")
        if (data.get("error") != SOURCE_CHANGED_ERROR or data.get("source") != "uploaded" or
                type(offset) is not int or type(size) is not int or not 0 <= offset < size or
                not isinstance(data.get("video_id"), str) or
                not isinstance(data.get("storage_path"), str) or
                not str(data.get("tus_url", "")).startswith(
                    "https://adscteqgwmvherkctzfm.supabase.co/storage/v1/upload/resumable/") or
                data.get("snapshot_paths") or data.get("remote_project_id") or
                data.get("blocked_from_stage") not in (None, "upload_video") or
                conn.execute("SELECT 1 FROM migration_links WHERE project_id=?", (pid,)).fetchone() or
                conn.execute("SELECT 1 FROM bridge_jobs WHERE project_id=? AND state IN ('queued','running')",
                             (pid,)).fetchone()):
            raise ValueError("迁移阶段或远端关联不符合安全续传条件")
        if not matching_source(data, project):
            raise ValueError("原视频路径、大小或修改时间与迁移记录不一致")
        validate_project(TrackerProject.model_validate(project), conn)
        data["project_updated_at"] = project["updated_at"]
        data.pop("error", None)
        conn.execute("UPDATE migration_workflows SET stage='upload_video',payload=?,next_run=0,updated_at=? "
                     "WHERE project_id=?", (json.dumps(data), vt.now(), pid))
        return {"project_id": pid, "stage": "upload_video", "upload_offset": offset,
                "source_size": size, "remote_video_id": data["video_id"]}


@router.post("/worker/claim")
def claim(request: Request, body: ClaimRequest | None = None):
    authorize(request)
    if accounts.ENFORCE_AUTH and not extension_supports_version(body.extension_version if body else None, "1.5.0"):
        raise HTTPException(409, "账号 Token 系统需要后台扩展 1.5.0")
    now = time.time()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO bridge_settings VALUES ('heartbeat', ?)", (str(now),))
        if body and body.extension_version:
            conn.execute("INSERT OR REPLACE INTO bridge_settings VALUES ('extension_version', ?)",
                         (body.extension_version,))
        expired = conn.execute("SELECT id,project_id,kind FROM bridge_jobs WHERE state='running' AND lease_until < ?", (now,)).fetchall()
        for old in expired:
            if old["kind"] == "migrate":
                row = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?", (old["project_id"],)).fetchone()
                if row and row["stage"] in {"init_video", "finalize_video", "setup", "create_project", "confirm_tracking"}:
                    data = json.loads(row["payload"])
                    data["error"] = "远端写入结果不确定；已停止自动重试，请人工核对"
                    conn.execute("UPDATE migration_workflows SET stage='uncertain',payload=?,updated_at=? WHERE project_id=?",
                                 (json.dumps(data), vt.now(), old["project_id"]))
                    conn.execute("UPDATE bridge_jobs SET state='failed',error=?,finished=?,lease=NULL WHERE id=?",
                                 (data["error"], now, old["id"]))
                    continue
            conn.execute("UPDATE bridge_jobs SET state='queued',lease=NULL WHERE id=?", (old["id"],))
        workflows = conn.execute("""SELECT w.project_id,w.stage,w.payload,p.payload AS project_payload
            FROM migration_workflows w JOIN projects p ON p.id=w.project_id
            WHERE w.next_run<=? AND w.stage IN
            ('lookup_video','init_video','start_upload','upload_video','finalize_video',
             'wait_video','upload_snapshots','setup','create_project','estimate','confirm_tracking')
            ORDER BY w.updated_at""", (now,)).fetchall()
        for workflow in workflows:
            if not accounts.task_enabled(workflow["project_id"]):
                continue
            data = json.loads(workflow["payload"])
            if not extension_supports_version(body.extension_version if body else None,
                                               required_version(workflow["stage"], data)):
                continue
            if workflow["stage"] == "confirm_tracking" and accounts.ENFORCE_AUTH:
                try:
                    token_wallet.reserve(conn, workflow["project_id"], data["remote_project_id"], data["estimated_cost"])
                except HTTPException as exc:
                    # A request queued before wallet rollout still needs a hold.
                    data["error"] = exc.detail
                    conn.execute("UPDATE migration_workflows SET stage='await_confirmation',payload=?,updated_at=? WHERE project_id=?",
                                 (json.dumps(data), vt.now(), workflow["project_id"]))
                    continue
            if workflow["stage"] == "create_project":
                existing = conn.execute("SELECT payload FROM migration_links WHERE project_id=?",
                                        (workflow["project_id"],)).fetchone()
                if existing:
                    data = json.loads(workflow["payload"])
                    data["remote_project_id"] = json.loads(existing[0])["remote_project_id"]
                    conn.execute("UPDATE migration_workflows SET stage='estimate',payload=?,updated_at=? WHERE project_id=?",
                                 (json.dumps(data), vt.now(), workflow["project_id"]))
                    continue
            latest = conn.execute("SELECT * FROM bridge_jobs WHERE project_id=? ORDER BY created DESC LIMIT 1",
                                  (workflow["project_id"],)).fetchone()
            if latest and latest["state"] in ("queued", "running"):
                continue
            data = json.loads(workflow["payload"])
            conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,kind) VALUES (?,?,?,'queued',?,'migrate')",
                         (uuid.uuid4().hex, workflow["project_id"], data.get("remote_project_id", ""), now))
        links = conn.execute("SELECT m.project_id,m.payload FROM migration_links m JOIN projects p ON p.id=m.project_id").fetchall()
        for row in links:
            if not accounts.task_enabled(row[0]):
                continue
            workflow = conn.execute("SELECT stage FROM migration_workflows WHERE project_id=?", (row[0],)).fetchone()
            if workflow and workflow["stage"] not in ("processing", "complete"):
                continue
            link = json.loads(row[1])
            # Ambiguous associations are not synchronized.
            if sum(json.loads(r[1])["remote_project_id"] == link["remote_project_id"] for r in links) != 1:
                continue
            latest = conn.execute("SELECT * FROM bridge_jobs WHERE project_id=? ORDER BY created DESC LIMIT 1", (row[0],)).fetchone()
            if latest and (latest["state"] in ("queued", "running") or now - latest["created"] < 300):
                continue
            kind = "sync_status" if link.get("association", {}).get("verified") else "inspect_remote"
            conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,kind) VALUES (?,?,?,'queued',?,?)",
                         (uuid.uuid4().hex, row[0], link["remote_project_id"], now, kind))
        candidates = conn.execute("SELECT * FROM bridge_jobs WHERE state='queued' AND (kind<>'check_video' OR ?) ORDER BY CASE WHEN kind='check_video' THEN 0 ELSE 1 END,created",
                           (extension_supports_version(body.extension_version if body else None, VIDEO_CHECK_EXTENSION_VERSION),)).fetchall()
        job = next((r for r in candidates if accounts.task_enabled(r["project_id"])), None)
        if not job:
            return {"job": None}
        lease = secrets.token_urlsafe(24)
        conn.execute("UPDATE bridge_jobs SET state='running',lease=?,lease_until=? WHERE id=?",
                     (lease, now + (600 if job["kind"] == "migrate" else 90), job["id"]))
        # Bounded history; never remove pending work.
        conn.execute("DELETE FROM bridge_jobs WHERE state IN ('done','failed') AND created < ?", (now - 30 * 86400,))
        result = {"id": job["id"], "kind": job["kind"], "lease": lease,
                  "expected_ggab_subject": accounts.source_subject(),
                  "shared_local_users": accounts.ENFORCE_AUTH,
                  "local_project_id": job["project_id"],
                  "remote_project_id": job["remote_id"],
                  "remote_url": "https://app.getgoodatbadminton.com/tracker/" + job["remote_id"]}
        if job["kind"] in {"sync_status", "check_video"}:
            try:
                result["video"] = video_target(conn, job["project_id"])
            except HTTPException:
                if job["kind"] == "check_video":
                    conn.execute("UPDATE bridge_jobs SET state='failed',error=?,finished=?,lease=NULL WHERE id=?",
                                 ("视频关联已变化，请重新核对", now, job["id"]))
                    return {"job": None}
        if job["kind"] == "sync_status":
            # Finalized projects remain eligible after their first export and
            # legacy verified associations need no migration workflow to sync.
            result["needs_export"] = True
        if job["kind"] == "migrate":
            workflow = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?",
                                    (job["project_id"],)).fetchone()
            project = json.loads(conn.execute("SELECT payload FROM projects WHERE id=?", (job["project_id"],)).fetchone()[0])
            data = json.loads(workflow["payload"])
            source = matching_source(data, project)
            if not source or not accept_project_revision(conn, job["project_id"], workflow["stage"], data, project):
                conn.execute("UPDATE migration_workflows SET stage='blocked',payload=?,updated_at=? WHERE project_id=?",
                             (json.dumps({**data, "error": SOURCE_CHANGED_ERROR,
                                          "blocked_from_stage": workflow["stage"]}), vt.now(), job["project_id"]))
                conn.execute("UPDATE bridge_jobs SET state='failed',error=?,finished=?,lease=NULL WHERE id=?",
                             (SOURCE_CHANGED_ERROR, now, job["id"]))
                return {"job": None}
            record, path, stat = source
            result.update(local_project_id=job["project_id"], stage=workflow["stage"], data=data,
                          project=project, video={"filename": record.get("filename", path.name),
                                                  "duration": record["duration"], "size_bytes": stat.st_size})
        return {"job": result}


class VideoObservation(BaseModel):
    filename: str | None = Field(None, max_length=500)
    metadata_size_bytes: int | None = Field(None, ge=0)
    original_size_bytes: int | None = Field(None, ge=0)
    duration: float | None = Field(None, ge=0, allow_inf_nan=False)


class VideoCheck(BaseModel):
    video_id: str = Field(max_length=100)
    status: Literal["ready", "missing", "failed", "processing", "mismatch", "unknown"]
    reason: str = Field(max_length=500)
    rule_version: int = Field(1, ge=1, le=2)
    observed: VideoObservation | None = None


class BillingReceipt(BaseModel):
    remote_project_id: str = Field(min_length=1, max_length=100)
    actual_tokens: int = Field(ge=0, le=token_wallet.MAX_TOKENS, strict=True)
    receipt_id: str = Field(min_length=1, max_length=200)
    source: Literal["ggab_token_ledger", "ggab_project_api"]


class Result(BaseModel):
    lease: str = Field(max_length=100)
    status: Literal["review", "processing", "finalized", "failed"] | None = None
    error: str | None = Field(None, max_length=500)
    diagnostic: dict | list | None = None
    progress: dict | None = None
    video_check: VideoCheck | None = None
    billing: BillingReceipt | None = None
    hold_transaction_id: str | None = Field(None, min_length=1, max_length=200)


@router.post("/worker/jobs/{job_id}/result")
def finish(job_id: str, body: Result, request: Request):
    authorize(request)
    if body.progress is not None:
        if body.status or body.error:
            raise HTTPException(422, "迁移进度不能与状态或错误同时提交")
    elif not body.video_check and bool(body.status) == bool(body.error):
        raise HTTPException(422, "必须提供明确状态或错误，不能同时提供")
    elif body.status and body.error:
        raise HTTPException(422, "不能同时提供状态和错误")
    if body.billing and (body.status not in {"finalized", "failed"} or body.error or body.progress):
        raise HTTPException(422, "只有已核验的最终结果可以结算真实费用")
    now = time.time()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT * FROM bridge_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["state"] != "running" or job["lease_until"] < now or not secrets.compare_digest(job["lease"], body.lease):
            raise HTTPException(409, "任务租约已失效")
        if body.video_check and job["kind"] == "sync_status" and not (body.status or body.error):
            raise HTTPException(422, "状态同步必须提供明确状态或错误")
        if body.video_check:
            if job["kind"] not in {"check_video", "sync_status"}:
                raise HTTPException(422, "此任务不能提交视频检测结果")
            target = video_target(conn, job["project_id"])
            if body.video_check.video_id != target["video_id"]:
                raise HTTPException(409, "视频关联已变化，忽略旧检测结果")
            linked = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (job["project_id"],)).fetchone()
            if (json.loads(linked[0])["remote_project_id"] if linked else "") != job["remote_id"]:
                raise HTTPException(409, "项目关联已变化，忽略旧检测结果")
            # An old worker must not overwrite corrected evidence with the
            # metadata-only 1.4.2 detector's false positives.
            if body.video_check.rule_version >= 2:
                checked_at = vt.now()
                conn.execute("INSERT OR REPLACE INTO video_checks VALUES (?,?,?)", (job["project_id"],
                             json.dumps({**body.video_check.model_dump(), "checked_at": checked_at}), checked_at))
        if job["kind"] == "check_video":
            if not body.video_check and not body.error:
                raise HTTPException(422, "缺少视频检测结果")
            conn.execute("UPDATE bridge_jobs SET state=?,error=?,finished=?,lease=NULL WHERE id=?",
                         ("failed" if body.error else "done", body.error, now, job_id))
            return {"ok": not bool(body.error), "error": body.error}
        if job["kind"] == "migrate":
            if body.billing:
                raise HTTPException(422, "迁移估算不能作为真实扣费")
            workflow = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?",
                                    (job["project_id"],)).fetchone()
            if not workflow:
                raise HTTPException(409, "迁移任务已不存在")
            stage = workflow["stage"]
            data = json.loads(workflow["payload"])
            transitions = {
                "lookup_video": {"init_video", "upload_snapshots"},
                "init_video": {"start_upload"}, "start_upload": {"upload_video"},
                "upload_video": {"upload_video", "finalize_video"},
                "finalize_video": {"wait_video"}, "wait_video": {"wait_video", "upload_snapshots"},
                "upload_snapshots": {"upload_snapshots", "setup"},
                "setup": {"create_project"}, "create_project": {"estimate"},
                "estimate": {"await_confirmation"}, "confirm_tracking": {"processing", "await_confirmation"},
            }
            error = body.error
            next_stage = body.progress.get("stage") if body.progress else None
            delta = body.progress.get("data", {}) if body.progress else {}
            previous_offset = data.get("upload_offset", 0)
            previous_snapshots = len(data.get("snapshot_paths", {}))
            if body.status or (body.progress and (next_stage not in transitions.get(stage, set()) or not isinstance(delta, dict))):
                raise HTTPException(422, "无效的迁移阶段")
            if stage == "create_project" and next_stage == "estimate":
                link = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (job["project_id"],)).fetchone()
                if not link or json.loads(link[0]).get("remote_project_id") != data.get("remote_project_id"):
                    error = "远端项目 ID 尚未持久化，停止后续操作"
            if stage in {"estimate", "confirm_tracking"} and next_stage == "await_confirmation":
                cost = delta.get("estimated_cost")
                if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
                    raise HTTPException(422, "缺少有效的 GGAB 费用估算")
                insufficient = (delta.get("insufficient_ggab_balance") is True and type(delta.get("balance_before")) is int and delta["balance_before"] < cost)
                if stage == "confirm_tracking" and not insufficient and (delta.get("estimate_changed_from") != data.get("estimated_cost") or
                        cost == data.get("estimated_cost")):
                    raise HTTPException(422, "GGAB 新旧费用不符合变更记录")
                link = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (job["project_id"],)).fetchone()
                if not link:
                    error = "远端项目关联缺失，不能保存费用"
                else:
                    linked = json.loads(link[0])
                    if (linked.get("remote_project_id") != data.get("remote_project_id") or
                            linked.get("status") != "review" or
                            linked.get("association", {}).get("verified") is not True):
                        raise HTTPException(409, "远端项目或关联证据已变化，不能更新费用")
                    linked["estimated_cost"] = cost
                    linked["balance_before"] = delta.get("balance_before") if type(delta.get("balance_before")) is int else None
                    token_wallet.remember_balance(conn, linked["balance_before"])
                    linked["updated_at"] = vt.now()
                    conn.execute("UPDATE migration_links SET payload=?,updated_at=? WHERE project_id=?",
                                 (json.dumps(linked), linked["updated_at"], job["project_id"]))
                    project_row = conn.execute("SELECT payload FROM projects WHERE id=?", (job["project_id"],)).fetchone()
                    saved = json.loads(project_row[0]) if project_row else None
                    if saved and (not data.get("project_updated_at") or data["project_updated_at"] == saved.get("updated_at")):
                        cost_estimation.observe_quote(conn, saved, linked, event_id="job:" + job_id,
                                                      source="ggab_api", observed_at=linked["updated_at"])
            if body.progress and not error:
                if stage == "confirm_tracking" and next_stage == "await_confirmation":
                    token_wallet.release_unsubmitted(conn, job["project_id"])
                data.update(delta)
                data.pop("error", None)
                if stage == "confirm_tracking" and next_stage == "processing":
                    token_wallet.submitted(conn, job["project_id"])
                    data.pop("estimate_changed_from", None)
                    link_row = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (job["project_id"],)).fetchone()
                    if not link_row:
                        raise HTTPException(409, "远端项目关联缺失")
                    link = json.loads(link_row[0])
                    link.update(status="processing", updated_at=vt.now())
                    conn.execute("UPDATE migration_links SET payload=?,updated_at=? WHERE project_id=?",
                                 (json.dumps(link), link["updated_at"], job["project_id"]))
            elif error:
                data["error"] = error
                candidate = body.diagnostic.get("remote_project_id") if isinstance(body.diagnostic, dict) else None
                if stage == "create_project" and isinstance(candidate, str):
                    try:
                        data["remote_project_id"] = str(uuid.UUID(candidate))
                    except ValueError:
                        pass
                # Migration.session() runs before any remote request. Failure
                # there cannot have created/charged anything and can resume
                # safely after the existing GGAB page supplies a session.
                preflight_failure = error == SESSION_PREFLIGHT_ERROR
                if stage in {"init_video", "finalize_video", "setup", "create_project", "confirm_tracking"} and not preflight_failure:
                    next_stage = "uncertain"
                else:
                    next_stage = stage
            delay = 0 if body.progress and next_stage != stage else 60
            if body.progress and not error:
                if (stage == next_stage == "upload_video" and
                        isinstance(delta.get("upload_offset"), int) and
                        delta["upload_offset"] > previous_offset):
                    delay = 0
                elif (stage == next_stage == "upload_snapshots" and
                      isinstance(delta.get("snapshot_paths"), dict) and
                      len(delta["snapshot_paths"]) > previous_snapshots):
                    delay = 0
            if error and next_stage == "uncertain":
                delay = 0
            conn.execute("UPDATE migration_workflows SET stage=?,payload=?,next_run=?,updated_at=? WHERE project_id=?",
                         (next_stage, json.dumps(data), now + delay, vt.now(), job["project_id"]))
            conn.execute("UPDATE bridge_jobs SET state=?,error=?,diagnostic=?,finished=?,lease=NULL WHERE id=?",
                         ("failed" if error else "done", error,
                          json.dumps({"from_stage": stage, "to_stage": next_stage}), now, job_id))
            return {"ok": not bool(error), "error": error, "stage": next_stage}
        row = conn.execute("SELECT m.payload FROM migration_links m JOIN projects p ON p.id=m.project_id WHERE m.project_id=?", (job["project_id"],)).fetchone()
        error = body.error
        link = json.loads(row[0]) if row else None
        if not link or link["remote_project_id"] != job["remote_id"]:
            error = "项目关联已变更，忽略旧任务结果"
        elif job["kind"] == "sync_status" and not link.get("association", {}).get("verified"):
            error = "关联证据未核验，忽略远端状态"
        matches = conn.execute("SELECT m.payload FROM migration_links m JOIN projects p ON p.id=m.project_id").fetchall()
        if sum(json.loads(r[0])["remote_project_id"] == job["remote_id"] for r in matches) != 1:
            error = "远端项目关联不唯一，忽略任务结果"
        if body.status and not error and job["kind"] == "sync_status":
            if (link["status"] == "finalized" and body.status != "finalized") or (link["status"] == "processing" and body.status == "review"):
                error = "远端状态与本地进度冲突，保留原状态；请核对取消或重追记录"
            else:
                if body.hold_transaction_id:
                    token_wallet.bind_receipt(conn, job["project_id"], job["remote_id"], body.hold_transaction_id)
                if body.billing:
                    if body.billing.remote_project_id != job["remote_id"]:
                        raise HTTPException(409, "扣费凭据的项目 ID 不匹配")
                    token_wallet.settle(conn, job["project_id"], job["remote_id"], body.billing.actual_tokens,
                                        body.billing.receipt_id, body.billing.model_dump())
                link.update(status=body.status, updated_at=vt.now(), last_verified_at=vt.now())
                conn.execute("UPDATE migration_links SET payload=?,updated_at=? WHERE project_id=?",
                             (json.dumps(link), link["updated_at"], job["project_id"]))
                if body.status in {"finalized", "failed"}:
                    token_wallet.settle_ready(conn)
        diagnostic = json.dumps(body.diagnostic, ensure_ascii=False) if body.diagnostic is not None else None
        if diagnostic and len(diagnostic) > 20000:
            diagnostic = json.dumps({"truncated": True, "preview": diagnostic[:19000]}, ensure_ascii=False)
        conn.execute("UPDATE bridge_jobs SET state=?,error=?,observed_status=?,diagnostic=?,finished=?,lease=NULL WHERE id=?",
                     ("failed" if error else "done", error, body.status, diagnostic, now, job_id))
    return {"ok": not bool(error), "error": error}


def workflow_source(pid: str):
    with db() as conn:
        row = conn.execute("SELECT w.stage,w.payload AS workflow,p.payload AS project FROM migration_workflows w "
                           "JOIN projects p ON p.id=w.project_id WHERE w.project_id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "迁移项目不存在")
        data, project = json.loads(row["workflow"]), json.loads(row["project"])
        source = matching_source(data, project)
        if not source or not accept_project_revision(conn, pid, row["stage"], data, project):
            raise HTTPException(409, "本地项目或原视频已变化")
        record, path, _ = source
    return data, project, record, path


@router.get("/worker/projects/{pid}/video")
def worker_video(pid: str, request: Request):
    authorize(request)
    _, _, record, path = workflow_source(pid)
    return FileResponse(path, media_type="video/mp4", filename=record.get("filename", path.name),
                        content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@router.get("/worker/projects/{pid}/frame/{slot}")
def worker_frame(pid: str, slot: str, request: Request):
    authorize(request)
    _, project, _, path = workflow_source(pid)
    snapshot = project.get("snapshots", {}).get(slot)
    if not snapshot:
        raise HTTPException(404, "球员截图不存在")
    # GGAB receives the complete original frame. Its box is converted back to
    # unrotated coordinates by the extension; no additional video is created.
    return Response(frame_bytes(path, snapshot["time"], 0), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/worker/projects/{pid}/link")
def worker_link(pid: str, body: MigrationLink, request: Request):
    authorize(request)
    with db() as conn:
        row = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
    if not row or row["stage"] != "create_project":
        raise HTTPException(409, "项目不在创建关联阶段")
    data = json.loads(row["payload"])
    if body.source != data.get("source") or not body.evidence or body.evidence.remote_video_id != data.get("video_id"):
        raise HTTPException(422, "远端视频证据与迁移任务不匹配")
    if not association_for(load_project(pid), body)["verified"]:
        raise HTTPException(409, "视频关联证据未通过核验")
    linked = save_migration(pid, body)
    with db() as conn:
        current = conn.execute("SELECT payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
        saved = json.loads(current[0])
        saved["remote_project_id"] = body.remote_project_id
        conn.execute("UPDATE migration_workflows SET payload=?,updated_at=? WHERE project_id=?",
                     (json.dumps(saved), vt.now(), pid))
    return linked


class ConfirmRequest(BaseModel):
    remote_project_id: str
    estimated_cost: int = Field(ge=0, le=token_wallet.MAX_TOKENS, strict=True)


def confirmation_matches(data, link):
    cost = data.get("estimated_cost")
    return (type(cost) is int and cost >= 0 and link.get("estimated_cost") == cost and
            bool(data.get("remote_project_id")) and data["remote_project_id"] == link.get("remote_project_id") and
            link.get("status") == "review" and link.get("association", {}).get("verified") is True)


@router.post("/confirm/{pid}")
def confirm_tracking(pid: str, body: ConfirmRequest):
    accounts.require("project", pid)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
        link_row = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (pid,)).fetchone()
        if not row or row["stage"] != "await_confirmation" or not link_row:
            raise HTTPException(409, "项目尚未到费用确认阶段")
        link = json.loads(link_row[0])
        data = json.loads(row["payload"])
        checked = conn.execute("SELECT payload FROM video_checks WHERE project_id=?", (pid,)).fetchone()
        if checked:
            check = json.loads(checked[0])
            if (check.get("video_id") == video_target(conn, pid)["video_id"] and
                    check.get("status") in {"missing", "failed", "mismatch"}):
                raise HTTPException(409, "视频上传核验失败或片源不可用，请先检测并恢复视频；未提交自动跟踪")
        if (link["remote_project_id"] != body.remote_project_id or link.get("estimated_cost") != body.estimated_cost or
                not confirmation_matches(data, link)):
            raise HTTPException(409, "费用或远端项目已变化，请重新核对")
        observed = token_wallet.remote_balance(conn)
        balance = observed["total"] if not observed["stale"] else link.get("balance_before")
        if type(balance) is int and balance < body.estimated_cost:
            raise HTTPException(402, "GGAB 余额不足，需要管理员充值后重新读取费用")
        token_wallet.reserve(conn, pid, body.remote_project_id, body.estimated_cost)
        conn.execute("UPDATE migration_workflows SET stage='confirm_tracking',next_run=0,updated_at=? WHERE project_id=?",
                     (vt.now(), pid))
    return {"queued": True, "remote_project_id": body.remote_project_id, "estimated_cost": body.estimated_cost}


class ShotExport(BaseModel):
    remote_project_id: str
    shots: list[dict] = Field(max_length=100000)
    rallies: list[dict] = Field(default_factory=list, max_length=20000)


class FullExportStart(BaseModel):
    remote_project_id: str = Field(max_length=100)


class FullExportResult(BaseModel):
    csv_text: str | None = Field(None, max_length=50_000_000)
    error: str | None = Field(None, max_length=500)


def verified_video_links():
    """Map exact GGAB project/match IDs to existing local video sessions."""
    links, duplicates = {}, set()
    with db() as conn:
        for row in conn.execute("SELECT p.id,p.payload AS project,m.payload AS migration FROM projects p "
                                "JOIN migration_links m ON m.project_id=p.id"):
            project, link = json.loads(row["project"]), json.loads(row["migration"])
            remote = link.get("remote_project_id")
            if not remote or not link.get("association", {}).get("verified") or not project.get("session_id"):
                continue
            if link.get("session_id", project["session_id"]) != project["session_id"]:
                continue
            if remote in links:
                duplicates.add(remote)
            links[remote] = {"session_id": project["session_id"], "project_id": row["id"]}
    return {key: value for key, value in links.items() if key not in duplicates}


@router.post("/worker/projects/{pid}/full-export/start")
def start_full_export(pid: str, body: FullExportStart, request: Request):
    authorize(request)
    from app import DB_PATH
    with db() as conn:
        row = conn.execute("SELECT m.payload FROM migration_links m JOIN projects p ON p.id=m.project_id "
                           "WHERE m.project_id=?", (pid,)).fetchone()
        link = json.loads(row[0]) if row else {}
        matches = [r for r in conn.execute("SELECT payload FROM migration_links")
                   if json.loads(r[0]).get("remote_project_id") == body.remote_project_id]
    if (len(matches) != 1 or link.get("remote_project_id") != body.remote_project_id or
            link.get("status") != "finalized" or not link.get("association", {}).get("verified")):
        raise HTTPException(409, "只有已核验且已完成的 GGAB 项目能触发全量导出")
    return full_csv_sync.begin(DB_PATH, body.remote_project_id)


def mark_full_exported(result):
    with db() as conn:
        for row in conn.execute("SELECT w.project_id,w.payload,m.payload AS migration FROM migration_workflows w "
                                "JOIN migration_links m ON m.project_id=w.project_id "
                                "WHERE w.stage IN ('processing','complete')").fetchall():
            link = json.loads(row["migration"])
            if (link.get("status") == "finalized" and link.get("association", {}).get("verified") and
                    link.get("remote_project_id") in result["match_ids"]):
                data = json.loads(row["payload"])
                data.update(full_export_sha256=result["sha256"], full_exported_at=result["imported_at"])
                conn.execute("UPDATE migration_workflows SET stage='complete',payload=?,updated_at=? WHERE project_id=?",
                             (json.dumps(data), vt.now(), row["project_id"]))


@router.post("/worker/full-export/{ticket}")
def finish_full_export(ticket: str, body: FullExportResult, request: Request):
    authorize(request)
    from app import DB_PATH
    if (body.csv_text is None) == (body.error is None):
        raise HTTPException(422, "必须提供全量 CSV 或导出错误")
    try:
        if body.error is not None:
            full_csv_sync.fail(DB_PATH, ticket, body.error)
            return {"ok": False}
        result = full_csv_sync.apply_snapshot(DB_PATH, ticket, body.csv_text, verified_video_links())
    except (ValueError, csv.Error, OSError) as exc:
        try:
            full_csv_sync.fail(DB_PATH, ticket, str(exc))
        except ValueError:
            pass
        raise HTTPException(409, str(exc)) from exc
    mark_full_exported(result)
    return {"ok": True, **result}


@router.get("/full-export.csv")
def download_full_export():
    accounts.admin_required()
    from app import DB_PATH
    saved = full_csv_sync.status(DB_PATH)
    path = Path(saved.get("path", full_csv_sync.CSV_PATH))
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != saved.get("sha256"):
        raise HTTPException(404, "尚无已成功同步的全量 CSV")
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename=path.name,
                        headers={"Cache-Control": "no-store"})


def csv_cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.post("/worker/projects/{pid}/shots-export")
def worker_export_shots(pid: str, body: ShotExport, request: Request):
    authorize(request)
    if not body.shots:
        raise HTTPException(409, "GGAB 尚未提供逐拍标注；不生成空 CSV")
    data, project, record, _ = workflow_source(pid)
    with db() as conn:
        linked = conn.execute("SELECT payload FROM migration_links WHERE project_id=?", (pid,)).fetchone()
    if not linked:
        raise HTTPException(409, "GGAB 项目尚未关联")
    link = json.loads(linked[0])
    if (link["remote_project_id"] != body.remote_project_id or link.get("status") != "finalized" or
            not link.get("association", {}).get("verified")):
        raise HTTPException(409, "远端关联或追踪状态未经核验")
    for item in (*body.shots, *body.rallies):
        if any(str(item.get(k)) not in ("None", body.remote_project_id) for k in ("projectId", "project_id") if k in item):
            raise HTTPException(422, "GGAB 标注包含其他项目 ID")
    rally_by_id = {str(r.get("id")): r for r in body.rallies if r.get("id") is not None}
    shot_fields = sorted({key for shot in body.shots for key in shot if key not in ("projectId", "project_id")})
    rally_fields = sorted({key for rally in body.rallies for key in rally if key not in ("projectId", "project_id")})
    fixed = ["local_project_id", "remote_project_id", "session_id", "video_filename", "match_title", "match_date", "game_type"]
    columns = fixed + ["shot_" + key for key in shot_fields] + ["rally_" + key for key in rally_fields]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for shot in body.shots:
        rally_id = shot.get("rallyId", shot.get("rally_id"))
        rally = rally_by_id.get(str(rally_id), {})
        row = {"local_project_id": pid, "remote_project_id": body.remote_project_id,
               "session_id": project["session_id"], "video_filename": record.get("filename", ""),
               "match_title": project["title"], "match_date": project.get("match_date"),
               "game_type": project["game_type"]}
        row.update({"shot_" + key: shot.get(key) for key in shot_fields})
        row.update({"rally_" + key: rally.get(key) for key in rally_fields})
        writer.writerow({key: csv_cell(value) for key, value in row.items()})
    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    directory = Path(__file__).resolve().parent / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ggab-shots-{pid}-{body.remote_project_id}.csv"
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".ggab-shots-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(content)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    with db() as conn:
        row = conn.execute("SELECT stage,payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
        if row:
            data = json.loads(row["payload"])
            data.update(exported_csv=str(path), exported_sha256=digest,
                        exported_shots=len(body.shots), exported_at=vt.now())
            conn.execute("UPDATE migration_workflows SET stage=?,payload=?,updated_at=? WHERE project_id=?",
                         ("complete" if row["stage"] == "processing" else row["stage"],
                          json.dumps(data), vt.now(), pid))
    return {"path": str(path), "shots": len(body.shots), "sha256": digest}


@router.get("/projects/{pid}/shots.csv")
def download_export(pid: str):
    with db() as conn:
        row = conn.execute("SELECT payload FROM migration_workflows WHERE project_id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "此项目尚无逐拍 CSV")
    data = json.loads(row[0])
    path = Path(data.get("exported_csv", ""))
    if not path.is_file() or path.parent != (Path(__file__).resolve().parent / "exports"):
        raise HTTPException(404, "此项目尚无逐拍 CSV")
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename=path.name)
