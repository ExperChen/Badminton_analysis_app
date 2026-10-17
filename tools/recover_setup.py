"""One bounded retry for an explicitly rejected setup request; never re-upload."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("video_id")
    parser.add_argument("--media-root", type=Path, default=Path(__file__).resolve().parents[1] / "media")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fixed-segments", action="store_true",
                        help="Resume only with extension 1.3.3 after verified near/far enum fix")
    parser.add_argument("--session-only", action="store_true",
                        help="Recover a legacy preflight auth failure that issued no remote requests")
    parser.add_argument("--accept-current-project", action="store_true",
                        help="Use current settings only when the source and all uploaded snapshot identities still match")
    args = parser.parse_args()
    database = args.media_root / "tracker" / "projects.db"
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM migration_workflows WHERE project_id=?", (args.project_id,)).fetchone()
        if not row or row["stage"] != "uncertain":
            raise ValueError("Expected a paused, uncertain workflow")
        data = json.loads(row["payload"])
        latest = conn.execute("SELECT * FROM bridge_jobs WHERE project_id=? AND kind='migrate' ORDER BY created DESC LIMIT 1",
                              (args.project_id,)).fetchone()
        diagnostic = json.loads(latest["diagnostic"] or "{}") if latest else {}
        expected_error = "GGAB 登录会话不可用；请在同一浏览器登录并刷新已有 GGAB 页面"
        rejected = latest and ((args.session_only and latest["error"] == expected_error) or
                               (not args.session_only and "HTTP 400" in (latest["error"] or "")))
        if not latest or latest["state"] != "failed" or diagnostic.get("from_stage") != "setup" or not rejected:
            raise ValueError("Only an explicit HTTP 400 setup rejection can be retried")
        if conn.execute("SELECT 1 FROM migration_links WHERE project_id=?", (args.project_id,)).fetchone():
            raise ValueError("Existing remote project must be reused separately")
        if conn.execute("SELECT 1 FROM bridge_jobs WHERE project_id=? AND state IN ('queued','running')", (args.project_id,)).fetchone():
            raise ValueError("A worker already owns this project")
        retry_flag = ("setup_session_recovery" if args.session_only else
                      "setup_segment_fix_retry" if args.fixed_segments else "setup_diagnostic_retry")
        if data.get(retry_flag) or data.get("video_id") != args.video_id:
            raise ValueError("Retry already used or remote video differs")
        if data.get("upload_offset") != data.get("source_size"):
            raise ValueError("Original upload is incomplete")
        source = Path(data["source_path"])
        stat = source.stat()
        project = json.loads(conn.execute("SELECT payload FROM projects WHERE id=?", (args.project_id,)).fetchone()[0])
        if args.fixed_segments and ("/api/tracker/setup" not in latest["error"] or "Invalid input" not in latest["error"] or
                                    not project.get("segments") or any(s["first_server"] not in ("near", "far") for s in project["segments"])):
            raise ValueError("Expected the verified GGAB segment enum rejection")
        if stat.st_size != data["source_size"] or stat.st_mtime_ns != data["source_mtime_ns"]:
            raise ValueError("Local video or project changed")
        if project["updated_at"] != data["project_updated_at"] and not args.accept_current_project:
            raise ValueError("Project revision changed; revalidate current settings first")
        if set(data.get("snapshot_paths", {})) != {p["slot"] for p in project["players"]}:
            raise ValueError("Saved remote player snapshots are incomplete")
        for player in project["players"]:
            snapshot = project["snapshots"][player["slot"]]
            suffix = player["slot"] + "-" + args.project_id + "-" + snapshot["id"] + ".jpg"
            if (not data["snapshot_paths"][player["slot"]].endswith("/" + suffix) or
                    player["snapshot_id"] != snapshot["id"] or snapshot["session_id"] != project["session_id"] or
                    not (args.media_root / "tracker" / (snapshot["id"] + ".jpg")).is_file()):
                raise ValueError("An uploaded player snapshot no longer matches the current project")
        print(json.dumps({"project_id":args.project_id, "video_id":args.video_id,
                          "from_stage":"uncertain", "to_stage":"setup", "apply":args.apply}))
        if args.apply:
            data.pop("error", None)
            data[retry_flag] = 1
            data["project_updated_at"] = project["updated_at"]
            if args.fixed_segments:
                data["required_extension_version"] = "1.3.3"
            conn.execute("UPDATE migration_workflows SET stage='setup',payload=?,next_run=0,updated_at=? WHERE project_id=?",
                         (json.dumps(data), datetime.now(timezone.utc).isoformat(), args.project_id))


if __name__ == "__main__":
    main()
