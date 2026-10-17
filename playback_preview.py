"""Optional, lower-cost browser playback cache; originals remain authoritative."""
import hashlib
import os
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import video_tools as videos
from compute_resources import CPU_THREADS, ENCODING_LOCK

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playback-preview")
LOCK = threading.RLock()
JOBS = {}


def target(session_id):
    record, source = videos.source(session_id)
    stat = source.stat()
    signature = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    digest = hashlib.sha256(signature.encode()).hexdigest()[:20]
    safe_id = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    path = videos.MEDIA_ROOT / "previews" / f"{safe_id}-{digest}.mp4"
    return record, source, signature, path


def status(session_id):
    _, _, signature, path = target(session_id)
    with LOCK:
        job = JOBS.get(session_id)
        if job and job["signature"] == signature and job["state"] == "processing":
            return {"state": "processing", "progress": job["progress"]}
        if path.is_file() and path.stat().st_size > 0:
            return {"state": "ready", "progress": 100}
        if job and job["signature"] == signature and job["state"] == "failed":
            return {"state": "failed", "error": job["error"]}
    return {"state": "idle"}


def start(session_id):
    record, source, signature, path = target(session_id)
    with LOCK:
        current = status(session_id)
        if current["state"] in ("ready", "processing"):
            return current
        JOBS[session_id] = {"signature": signature, "state": "processing", "progress": 0}
        POOL.submit(_encode, session_id, source, signature, path, record["duration"])
    return {"state": "processing", "progress": 0}


def _encode(session_id, source, signature, path, duration):
    with ENCODING_LOCK:
        return _encode_locked(session_id, source, signature, path, duration)


def _encode_locked(session_id, source, signature, path, duration):
    temp = path.parent / f".{path.stem}-{uuid.uuid4().hex}.mp4"
    command = [videos.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-threads", str(CPU_THREADS), "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "fps=30,scale=1280:-2:flags=fast_bilinear", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "27", "-maxrate", "4M", "-bufsize", "8M",
               "-g", "60", "-pix_fmt", "yuv420p", "-threads:v", str(CPU_THREADS),
               "-filter_threads", str(CPU_THREADS), "-c:a", "aac",
               "-b:a", "128k", "-movflags", "+faststart", "-progress", "pipe:1",
               "-y", str(temp)]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, creationflags=flags) as process:
            for line in process.stdout:
                if line.startswith("out_time_ms="):
                    try:
                        percent = min(99, int(float(line.split("=", 1)[1]) / 1_000_000 / duration * 100))
                    except (ValueError, ZeroDivisionError):
                        continue
                    with LOCK:
                        if JOBS.get(session_id, {}).get("signature") == signature:
                            JOBS[session_id]["progress"] = max(JOBS[session_id]["progress"], percent)
            if process.wait() != 0 or not temp.is_file() or temp.stat().st_size == 0:
                raise RuntimeError("生成流畅预览失败，请继续使用原片播放")
        if target(session_id)[2] != signature:
            raise RuntimeError("原视频已变化，流畅预览已取消")
        os.replace(temp, path)
        with LOCK:
            if JOBS.get(session_id, {}).get("signature") == signature:
                JOBS[session_id].update(state="ready", progress=100)
    except Exception as exc:
        with LOCK:
            if JOBS.get(session_id, {}).get("signature") == signature:
                JOBS[session_id].update(state="failed", error=str(exc)[:200])
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
