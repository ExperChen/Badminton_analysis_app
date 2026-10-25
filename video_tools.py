"""Persistent local video sessions and background, frame-accurate rally exports."""

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
import accounts
from compute_resources import CPU_THREADS, ENCODING_LOCK
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MEDIA_ROOT = ROOT / "media"
MEDIA_ROOT = Path(os.environ.get("YUXU_MEDIA_ROOT", DEFAULT_MEDIA_ROOT)).resolve()
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rally-export")
RUN_ID = uuid.uuid4().hex
ACTIVE = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def registry():
    with LOCK:
        path = MEDIA_ROOT / "sessions.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def register(session_id, path, filename, info, title=None):
    with LOCK:
        records = registry()
        old = records.get(session_id, {})
        if old:
            accounts.require("session", session_id)
        if not accounts.owner("session", session_id):
            accounts.claim("session", session_id)
        if (old.get("path") == str(path.resolve()) and old.get("filename") == filename and
                old.get("title") == (title or filename) and old.get("duration") == info["duration"] and
                old.get("has_audio") == info["has_audio"]):
            return old
        records[session_id] = {"session_id": session_id, "title": title or filename,
            "filename": filename, "path": str(path.resolve()), "duration": info["duration"],
            "has_audio": info["has_audio"], "created_at": old.get("created_at", now()),
            "updated_at": now()}
        save_json(MEDIA_ROOT / "sessions.json", records)
        return records[session_id]


class VideoNameConflict(ValueError):
    """An automatic same-name match must not replace a different source file."""


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_signature(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def import_original(temp, output, session_id, filename, title, size, digest, protect_existing=False):
    """Publish one playable original per distinct content; never replace an existing file."""
    media_root = MEDIA_ROOT.resolve()
    originals = (MEDIA_ROOT / "originals").resolve()
    if (temp.resolve().parent != originals or output.resolve().parent != originals or
            output.suffix.lower() not in VIDEO_EXTENSIONS):
        raise ValueError("视频目标目录无效。")
    with LOCK:
        records = registry()
        current = records.get(session_id)
        if protect_existing and current:
            current_path = Path(current["path"])
            if current_path.is_file():
                current_size = current_path.stat().st_size
                if current_size != size or file_sha256(current_path) != digest:
                    raise VideoNameConflict("同名视频内容不同，未替换已有 Session。请在目标 Session 中明确选择更换原视频。")
        preferred = [Path(current["path"])] if current else []
        referenced = [Path(record["path"]) for record in records.values()]
        candidates = preferred + referenced + sorted(media_root.iterdir()) + sorted(originals.iterdir())
        seen = set()
        for candidate in candidates:
            path = candidate.resolve()
            if (path in seen or candidate.is_symlink() or path.parent not in (media_root, originals) or
                    path.suffix.lower() not in VIDEO_EXTENSIONS):
                continue
            seen.add(path)
            try:
                before = path.stat()
                if before.st_size != size or file_sha256(path) != digest:
                    continue
                after = path.stat()
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    raise ValueError("已有原视频在核对时发生变化，未建立关联。")
                info = probe(path)
                return register(session_id, path, filename, info, title), True
            except FileNotFoundError:
                continue
        info = probe(temp)
        if output.exists():
            raise ValueError("视频目标文件已存在，未覆盖。")
        temp.replace(output)
        try:
            return register(session_id, output, filename, info, title), False
        except Exception:
            output.unlink(missing_ok=True)
            raise


def available_local_originals():
    """List unlinked videos only from the configured media root and originals folder."""
    with LOCK:
        root = MEDIA_ROOT.resolve()
        used = {str(Path(record["path"]).resolve()) for record in registry().values()}
        result = []
        for folder in (root, root / "originals"):
            if not folder.is_dir() or folder.is_symlink():
                continue
            for path in sorted(folder.iterdir()):
                if (path.is_symlink() or not path.is_file() or
                        path.suffix.lower() not in VIDEO_EXTENSIONS or
                        str(path.resolve()) in used):
                    continue
                size = path.stat().st_size
                if size > 0:
                    result.append({"relative_path": path.relative_to(root).as_posix(),
                                   "filename": path.name, "size_bytes": size})
        return result


def link_local_original(relative_path, session_id, filename, title, protect_existing=False):
    """Reference a disk original without copying it; refuse path traversal/symlinks."""
    root = MEDIA_ROOT.resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) not in (1, 2):
        raise ValueError("只能选择本地视频目录中的原片。")
    path = root / relative
    if (len(relative.parts) == 2 and relative.parts[0].lower() != "originals") or path.is_symlink():
        raise ValueError("只能选择本地视频目录中的原片。")
    actual = path.resolve()
    if (actual.parent not in (root, root / "originals") or
            actual.suffix.lower() not in VIDEO_EXTENSIONS or not actual.is_file()):
        raise ValueError("本地原视频不存在或不在允许的目录中。")
    with LOCK:
        records = registry()
        old = records.get(session_id)
        if old and Path(old["path"]).is_file():
            old_path = Path(old["path"]).resolve()
            if old_path != actual:
                old_signature = file_signature(old_path)
                new_signature = file_signature(actual)
                same_content = (old_path.stat().st_size == actual.stat().st_size and
                                file_sha256(old_path) == file_sha256(actual))
                if file_signature(old_path) != old_signature or file_signature(actual) != new_signature:
                    raise ValueError("原视频在核对时发生变化，未建立关联。")
                if not same_content and protect_existing:
                    raise VideoNameConflict("同名视频内容不同，未替换已有 Session。请在目标 Session 中明确选择更换原视频。")
                if same_content:
                    # The selected disk file really becomes the Session's source.
                    # Preserve its version so identical bytes do not invalidate snapshots.
                    records[session_id] = {**old, "path": str(actual)}
                    save_json(MEDIA_ROOT / "sessions.json", records)
                    return records[session_id]
        info = probe(actual)
        return register(session_id, actual, filename, info, title)


def reclaim_duplicate_original(session_id):
    """Rebind a UUID import to its identical named original, then remove only the duplicate.

    If the Session already points at the named original, an unreferenced UUID copy
    may be released instead. No file is removed unless one exact SHA-256 match is
    found, the retained file is stable, and no other Session uses the old path.
    """
    with LOCK:
        records = registry()
        record = records.get(session_id)
        if not record:
            raise ValueError("视频 Session 不存在。")
        if session_id in ACTIVE:
            raise ValueError("视频正在剪辑，未清理任何文件。")
        root = MEDIA_ROOT.resolve()
        originals = (root / "originals").resolve()
        current = Path(record["path"])
        retained = current.resolve()
        if (current.is_symlink() or not retained.is_file() or
                retained.parent not in (root, originals)):
            raise ValueError("当前原片不在允许的视频目录中，未清理。")
        current_is_uuid = bool(re.fullmatch(r"[0-9a-f]{32}", retained.stem)) and retained.parent == originals
        current_stat = retained.stat()
        current_signature = file_signature(retained)
        if current_stat.st_size <= 0:
            raise ValueError("当前原片为空，未清理。")
        used = {Path(value["path"]).resolve() for value in records.values()}
        candidates = []
        for folder in (root, originals):
            if not folder.is_dir() or folder.is_symlink():
                continue
            for path in folder.iterdir():
                if (path.is_symlink() or not path.is_file() or path.resolve() == retained or
                        path.suffix.lower() not in VIDEO_EXTENSIONS):
                    continue
                if current_is_uuid:
                    eligible = path.name.casefold() == record["filename"].casefold()
                else:
                    eligible = (folder == originals and path.resolve() not in used and
                                bool(re.fullmatch(r"[0-9a-f]{32}", path.stem)))
                if eligible and path.stat().st_size == current_stat.st_size:
                    candidates.append(path)
        if not candidates:
            raise ValueError("未找到同大小的可清理副本；没有修改或删除文件。")
        current_hash = file_sha256(retained)
        if file_signature(retained) != current_signature:
            raise ValueError("当前原片在核对时发生变化；没有修改或删除文件。")
        matches = []
        for candidate in candidates:
            before = candidate.stat()
            if file_sha256(candidate) == current_hash and file_signature(candidate) == (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                matches.append((candidate.resolve(), before))
        if len(matches) != 1:
            raise ValueError("未找到唯一且 SHA-256 完全一致的副本；没有修改或删除文件。")
        candidate, candidate_stat = matches[0]
        source = retained if current_is_uuid else candidate
        target = candidate if current_is_uuid else retained
        source_stat = current_stat if current_is_uuid else candidate_stat
        target_stat = candidate_stat if current_is_uuid else current_stat
        if any(key != session_id and Path(value["path"]).resolve() == source
               for key, value in records.items()):
            raise ValueError("旧副本仍被其他 Session 引用；没有修改或删除文件。")
        source_signature = (source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns)
        target_signature = (target_stat.st_dev, target_stat.st_ino, target_stat.st_size, target_stat.st_mtime_ns)
        if file_signature(source) != source_signature or file_signature(target) != target_signature:
            raise ValueError("原片在核对后发生变化；没有修改或删除文件。")
        if source_stat.st_ino and source_stat.st_dev == target_stat.st_dev and source_stat.st_ino == target_stat.st_ino:
            raise ValueError("两个路径是同一硬链接，没有可释放的重复数据。")
        if current_is_uuid:
            records[session_id] = {**record, "path": str(target)}
            save_json(MEDIA_ROOT / "sessions.json", records)
        try:
            if file_signature(source) != source_signature or file_signature(target) != target_signature:
                raise OSError("文件在最终检查时发生变化")
            source.unlink()
        except OSError as exc:
            return {"released": False, "released_bytes": 0, "session_id": session_id,
                    "retained_path": str(target), "duplicate_path": str(source),
                    "rebound": current_is_uuid, "reason": str(exc)}
        return {"released": True, "released_bytes": source_stat.st_size,
                "session_id": session_id, "retained_path": str(target),
                "duplicate_path": str(source), "rebound": current_is_uuid,
                "sha256": current_hash}


def public_video(record):
    if not record:
        return None
    result = {k: record[k] for k in ("filename", "duration", "has_audio", "updated_at")}
    if record.get("storage") == "google_drive":
        result["storage"] = "google_drive"
    return result


def remove_unmatched_session(session_id):
    """Remove registry entry and retain a recoverable copy under media/trash."""
    with LOCK:
        records = registry()
        record = records[session_id]
        if session_id in ACTIVE:
            raise ValueError("视频正在剪辑，请完成后再删除。")
        if record.get("storage") == "google_drive":
            del records[session_id]
            save_json(MEDIA_ROOT / "sessions.json", records)
            return "retained_drive"
        source_path = Path(record["path"]).resolve()
        if source_path.parent == MEDIA_ROOT.resolve():
            del records[session_id]
            save_json(MEDIA_ROOT / "sessions.json", records)
            return "retained_original"
        originals = (MEDIA_ROOT / "originals").resolve()
        if not source_path.is_relative_to(originals):
            raise ValueError("视频不在本项目导入目录中，未删除。")
        shared = any(key != session_id and Path(value["path"]).resolve() == source_path
                     for key, value in records.items())
        if shared:
            del records[session_id]
            save_json(MEDIA_ROOT / "sessions.json", records)
            return "retained_shared"
        trash_root = (MEDIA_ROOT / "trash").resolve()
        destination = (trash_root / uuid.uuid4().hex).resolve()
        if not destination.is_relative_to(trash_root):
            raise ValueError("回收目录无效。")
        destination.mkdir(parents=True)
        save_json(destination / "session.json", {**record, "deleted_at": now()})
        archived = destination / source_path.name
        moved = False
        try:
            if source_path.exists():
                source_path.rename(archived)
                moved = True
            del records[session_id]
            save_json(MEDIA_ROOT / "sessions.json", records)
            return "trashed"
        except Exception:
            if moved:
                archived.rename(source_path)
            raise


def binary(name):
    found = os.environ.get("YUXU_" + name.upper()) or shutil.which(name)
    if not found:
        raise ValueError(f"未找到 {name}，请安装 FFmpeg 并加入 PATH。")
    return found


def process(args, timeout=120, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, cwd=cwd,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise ValueError("视频处理失败，请确认文件完整且能被 FFmpeg 解码。" + result.stderr[-500:])
    return result


def probe(path):
    result = process([binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
                      "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)])
    meta = json.loads(result.stdout)
    streams = meta.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise ValueError("文件中没有可读取的视频轨道。")
    duration = float(meta.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法读取视频时长。")
    return {"duration": duration, "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def source(session_id):
    accounts.require("session", session_id)
    record = registry().get(session_id)
    if record and record.get("storage") == "google_drive":
        import drive_storage
        return record, drive_storage.cached(record)
    if not record or not Path(record["path"]).is_file():
        raise ValueError("这场 session 尚未关联视频，请先保存视频到本机。")
    return record, Path(record["path"])


def score_event(shots, start, end):
    """Scores belong to A/B sides, independently of the dashboard perspective."""
    final = max(shots, key=lambda s: (s.get("shot_number_in_rally") or 0,
                                      s.get("video_timestamp_seconds") or 0))
    def score(key):
        value = final.get(key)
        return str(int(value)) if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 and int(value) == value else "—"
    def team_name(team):
        return " / ".join(dict.fromkeys(
            s.get("striker_name") or s["striker"] for s in shots
            if str(s.get("striker") or "").startswith(team))) or team
    return {"start": max(start, end - 1.25), "end": end,
            "game": final.get("game_number"), "score_a": score("score_a_after"),
            "score_b": score("score_b_after"), "name_a": team_name("A"), "name_b": team_name("B")}


def ass_time(seconds):
    # ASS timestamps use centiseconds. Frame accuracy is then limited to output fps.
    ticks = max(0, round(seconds * 100))
    return f"{ticks // 360000}:{ticks // 6000 % 60:02d}:{ticks // 100 % 60:02d}.{ticks % 100:02d}"


def ass_text(value):
    # Prevent player names from being interpreted as ASS overrides or line breaks.
    return str(value).replace("\\", "／").replace("{", "（").replace("}", "）").replace("\r", " ").replace("\n", " ")[:40]


def write_score_subtitles(segment, path):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Score,Poppins,36,&H00FFFFFF,&H00FFFFFF,&H70000000,&H70000000,-1,0,0,0,100,100,0,0,3,10,0,7,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for event in segment.get("score_events", []):
        start = max(0, event["start"] - segment["start"])
        end = min(segment["end"], event["end"]) - segment["start"]
        if end <= start:
            continue
        fade_ms = min(250, int((end-start)*500))
        game = event["game"] if event["game"] is not None else "—"
        text = (f"{{\\fad({fade_ms},{fade_ms})\\fs24}}GAME {ass_text(game)}"
                f"{{\\fs36}}\\NA  {ass_text(event['name_a'])}   {event['score_a']}"
                f"\\NB  {ass_text(event['name_b'])}   {event['score_b']}")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Score,,0,0,0,,{text}\n")
    path.write_text(header + "".join(lines), encoding="utf-8")


def plan_clips(rows, duration, before=0, after=0):
    """Deduplicate repeated shot rows, validate boundaries, union overlapping intervals."""
    if not rows:
        raise ValueError("当前范围没有回合时间标注，无法自动剪辑。")
    groups = {}
    for row in rows:
        groups.setdefault((row["match_id"], row["rally_id"]), []).append(row)
    intervals = []
    invalid = []
    for (_, rally_id), shots in groups.items():
        pairs = set()
        for shot in shots:
            start, end = shot.get("rally_start_s"), shot.get("rally_end_s")
            if not isinstance(start, (float, int)) or not isinstance(end, (float, int)):
                invalid.append(rally_id)
                break
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration + .05:
                invalid.append(rally_id)
                break
            pairs.add((round(start, 6), round(end, 6)))
        else:
            if len(pairs) != 1:
                invalid.append(rally_id)
                continue
            start, end = next(iter(pairs))
            intervals.append({"start": max(0, start-before), "end": min(duration, end+after),
                              "rally_ids": [rally_id], "score_events": [score_event(shots, start, min(duration, end))]})
    if invalid:
        raise ValueError(f"{len(set(invalid))} 个回合的起止时间缺失、不一致或超出视频时长，请核对原始视频与标注；本次未剪辑。")
    merged = []
    for interval in sorted(intervals, key=lambda x: x["start"]):
        if merged and interval["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], interval["end"])
            merged[-1]["rally_ids"].extend(interval["rally_ids"])
            merged[-1]["score_events"].extend(interval["score_events"])
        else:
            merged.append(dict(interval))
    kept = sum(s["end"] - s["start"] for s in merged)
    return {"rally_count": len(groups), "segment_count": len(merged), "segments": merged,
            "source_duration": duration, "kept_duration": round(kept, 3),
            "removed_duration": round(max(0, duration-kept), 3), "before": before, "after": after,
            "score_overlay": {"enabled": True, "lead_seconds": 1.25,
                              "fade_in_seconds": .25, "fade_out_seconds": .25,
                              "score_source": "score_after", "position": "top_left"}}


def job_path(job_id):
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("找不到这个剪辑任务。")
    return MEDIA_ROOT / "exports" / job_id


def read_job(job_id):
    path = job_path(job_id) / "job.json"
    if not path.is_file():
        raise ValueError("找不到这个剪辑任务。")
    with LOCK:
        job = json.loads(path.read_text(encoding="utf-8"))
    if job["status"] in ("queued", "running") and job.get("run_id") != RUN_ID:
        job.update(status="failed", error="服务曾重启，剪辑已中断，请重新生成。")
    return job


def public_job(job):
    return {k: v for k, v in job.items() if k not in ("run_id", "source_path")}


def session_jobs(session_id):
    directory = MEDIA_ROOT / "exports"
    if not directory.exists():
        return []
    jobs = [read_job(p.parent.name) for p in directory.glob("*/job.json")]
    return [public_job(j) for j in sorted(jobs, key=lambda j: j["created_at"], reverse=True)
            if j["session_id"] == session_id][:20]


def create_job(session_id, source_path, plan, game, has_audio):
    binary("ffmpeg")
    with LOCK:
        if session_id in ACTIVE:
            return public_job(read_job(ACTIVE[session_id]))
        job_id = uuid.uuid4().hex
        accounts.claim("clip", job_id)
        job = {"id": job_id, "session_id": session_id, "status": "queued", "progress": 0,
               "created_at": now(), "run_id": RUN_ID, "game": game, "plan": plan,
               "source_path": str(source_path), "has_audio": has_audio}
        save_json(job_path(job_id) / "job.json", job)
        ACTIVE[session_id] = job_id
        POOL.submit(render_job, job)
        return public_job(job)


def render_job(job):
    with ENCODING_LOCK:
        return _render_job(job)


def _render_job(job):
    directory = job_path(job["id"])
    def update(**values):
        with LOCK:
            job.update(values)
            save_json(directory / "job.json", job)
    try:
        update(status="running")
        # Bundle the existing open font with the job so paths need no filter escaping.
        shutil.copyfile(ROOT / "static" / "vendor" / "poppins-semibold.ttf", directory / "score-font.ttf")
        segments = job["plan"]["segments"]
        mapping, offset = [], 0.0
        for index, segment in enumerate(segments):
            output = directory / f"part-{index:05d}.mp4"
            subtitle = directory / f"score-{index:05d}.ass"
            write_score_subtitles(segment, subtitle)
            filters = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30,setpts=PTS-STARTPTS,ass=filename={subtitle.name}:fontsdir=."
            args = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                    "-protocol_whitelist", "file,pipe", "-ss", str(segment["start"]),
                    "-i", job["source_path"], "-t", str(segment["end"]-segment["start"]),
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf", filters,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-threads", str(CPU_THREADS), "-filter_threads", str(CPU_THREADS)]
            if job["has_audio"]:
                args += ["-c:a", "aac", "-ar", "48000", "-af", "apad"]
            args += ["-map_metadata", "-1", str(output)]
            process(args, timeout=7200, cwd=directory)
            length = probe(output)["duration"]
            mapping.append({**segment, "output_start": round(offset, 6), "output_duration": length})
            offset += length
            update(progress=round((index+1)/len(segments)*90))
        # Generated filenames only; source paths never enter the concat file.
        (directory / "concat.txt").write_text("".join(f"file 'part-{i:05d}.mp4'\n" for i in range(len(segments))), encoding="utf-8")
        output = directory / "rallies.mp4"
        process([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                 "-f", "concat", "-safe", "1", "-i", str(directory / "concat.txt"),
                 "-c", "copy", "-movflags", "+faststart", str(output)], timeout=7200)
        actual = probe(output)["duration"]
        if abs(actual-job["plan"]["kept_duration"]) > max(1, len(segments)*.15):
            raise ValueError("输出时长与回合区间不符，未将结果标记为完成。")
        save_json(directory / "timeline.json", {"segments": mapping, "duration": actual})
        update(status="complete", progress=100, output_duration=actual,
               download_url=f'/api/clips/{job["id"]}/download', timeline_url=f'/api/clips/{job["id"]}/timeline')
        # Delete only generated intermediate clips, retaining original and final output.
        for i in range(len(segments)):
            (directory / f"part-{i:05d}.mp4").unlink(missing_ok=True)
    except Exception as exc:
        update(status="failed", error=str(exc)[-650:])
    finally:
        with LOCK:
            ACTIVE.pop(job["session_id"], None)
