"""Auditable local forecasts. GGAB quotes remain the only charge authority."""
import hashlib
import json
import logging
import math
from datetime import datetime, timezone

BASE_RATE = 10000 / 1800
ALGORITHM = "duration-calibration-v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cost_quotes (event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, remote_id TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS cost_quote_project ON cost_quotes(project_id, observed_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS cost_predictions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS cost_prediction_project ON cost_predictions(project_id, created_at)")


def features(project, duration, migration=None):
    """Use precise seconds, not the mm:ss display; never price unknown duration."""
    if not project.get("id") or not project.get("session_id") or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return None
    raw_segments = project.get("segments", [])
    if not isinstance(raw_segments, list) or any(not isinstance(s, dict) or
            not all(isinstance(s.get(k), (int, float)) and math.isfinite(s[k]) for k in ["start", "end"])
            for s in raw_segments):
        return None
    segments = sorted(raw_segments, key=lambda item: item["start"])
    end = 0
    for segment in segments:
        start, stop = segment["start"], segment["end"]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in [start, stop]):
            return None
        if start < end or stop <= start or stop > duration + .01:
            return None
        end = stop
    seconds = sum(s["end"] - s["start"] for s in segments) if segments else duration
    association = (migration or {}).get("association") or {}
    local = association.get("local_video") or {}
    identity = (local.get("fingerprint") or {}).get("value") or project.get("session_id")
    snapshots = project.get("snapshots", {})
    revision = next((s.get("video_version") for s in snapshots.values() if s.get("video_version")), None)
    setup = [project.get("session_id"), revision, project.get("game_type", "singles"),
             [[s["start"], s["end"]] for s in segments]]
    data = {"mode": "duration", "game_type": project.get("game_type", "singles"),
            "video_identity": identity, "duration_seconds": duration,
            "segments": setup[3], "effective_seconds": seconds,
            "setup_key": hashlib.sha256(json.dumps(setup, sort_keys=True).encode()).hexdigest()}
    data["features_key"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


def quote_features(project, migration):
    association = migration.get("association") or {}
    if association.get("verified") is not True:
        return None
    # Only use duration evidence from the exact verified original, not a title match.
    duration = (association.get("remote_video") or {}).get("duration")
    return features(project, duration, migration)


def model(conn, game_type):
    # One latest quote per remote project: repeated polls/reestimates are not new matches.
    rows = conn.execute("SELECT remote_id,payload FROM cost_quotes ORDER BY observed_at DESC,event_id DESC").fetchall()
    seen, samples = set(), []
    low, high = 0, float("inf")
    for row in rows:
        q = json.loads(row["payload"])
        if q["features"]["game_type"] != game_type or row["remote_id"] in seen:
            continue
        seen.add(row["remote_id"])
        seconds, cost = q["features"]["effective_seconds"], q["ggab_tokens"]
        # Bad/unknown-duration fallback quotes must not poison the local model.
        if cost <= 0 or not .1 * BASE_RATE <= cost / seconds <= 10 * BASE_RATE:
            continue
        lower, upper = (cost - 1) / seconds, cost / seconds
        next_low, next_high = max(low, lower), min(high, upper)
        if next_low >= next_high:
            break  # A new pricing regime: keep only the coherent recent suffix.
        low, high = next_low, next_high
        samples.append(q)
        if len(samples) == 20:
            break
    rate = BASE_RATE
    if samples:
        # Prefer simple rates (including a possible percent multiplier) only if
        # they reproduce EVERY integer quote in this recent regime.
        midpoint = (low + high) / 2
        simple = BASE_RATE * round(midpoint / BASE_RATE, 2)
        rate = BASE_RATE if low < BASE_RATE <= high else simple if low < simple <= high else midpoint
    version = hashlib.sha256(json.dumps([ALGORITHM, game_type, rate,
        [(q["features"]["features_key"], q["ggab_tokens"]) for q in samples]]).encode()).hexdigest()[:16]
    return {"algorithm": ALGORITHM, "version": version, "tokens_per_second": rate,
            "multiplier": rate / BASE_RATE, "sample_count": len(samples),
            "confidence": "historical_match" if len(samples) >= 3 else "tentative" if samples else "baseline",
            "authoritative": False}


def predict(conn, project, duration, migration=None):
    f = features(project, duration, migration)
    if f is None:
        return None
    m = model(conn, f["game_type"])
    data = {"features": f, "model": m, "tokens": math.ceil(f["effective_seconds"] * m["tokens_per_second"]),
            "baseline_tokens": math.ceil(f["effective_seconds"] * BASE_RATE), "authoritative": False}
    key = hashlib.sha256(json.dumps([project["id"], f["features_key"], m["version"]]).encode()).hexdigest()
    row = conn.execute("SELECT payload FROM cost_predictions WHERE id=?", (key,)).fetchone()
    if row:
        return json.loads(row["payload"])
    data.update(id=key, created_at=now())
    conn.execute("INSERT INTO cost_predictions VALUES (?,?,?,?)", (key, project["id"], data["created_at"], json.dumps(data)))
    logging.getLogger("badminton.estimation").info("local_prediction project=%s tokens=%s", project["id"], data["tokens"])
    return data


def observe_quote(conn, project, migration, *, event_id, source, observed_at=None):
    cost = migration.get("estimated_cost")
    if type(cost) is not int or cost < 0:
        return
    f = quote_features(project, migration)
    if not f:
        return
    prior = conn.execute("SELECT payload FROM cost_predictions WHERE project_id=? ORDER BY created_at DESC", (project["id"],)).fetchall()
    # A prediction must exist before a live quote. Legacy backfill cannot invent
    # a historical prediction or claim its own fitted result as a validation.
    observed_at = observed_at or now()
    prediction = next((json.loads(r[0]) for r in prior
                       if json.loads(r[0])["features"]["setup_key"] == f["setup_key"] and
                       json.loads(r[0])["created_at"] <= observed_at and
                       abs(json.loads(r[0])["features"]["duration_seconds"] - f["duration_seconds"]) <= max(.25, f["duration_seconds"] * .001)), None) if source == "ggab_api" else None
    data = {"features": f, "remote_project_id": migration["remote_project_id"],
            "remote_video_id": (migration.get("association", {}).get("remote_video") or {}).get("remote_video_id"),
            "ggab_tokens": cost, "baseline_tokens": math.ceil(f["effective_seconds"] * BASE_RATE),
            "prediction_tokens": prediction["tokens"] if prediction else None,
            "prediction_model": prediction["model"]["version"] if prediction else None,
            "prediction_error": prediction["tokens"] - cost if prediction else None,
            "source": source, "observed_at": observed_at, "recorded_at": now()}
    conn.execute("INSERT OR IGNORE INTO cost_quotes VALUES (?,?,?,?,?)", (event_id, project["id"],
                 migration["remote_project_id"], data["observed_at"], json.dumps(data)))


def backfill(conn):
    """Import existing verified quote records once. Never overwrite or re-date them."""
    rows = conn.execute("SELECT p.payload AS project,m.payload AS migration FROM projects p JOIN migration_links m ON m.project_id=p.id").fetchall()
    for row in rows:
        p, link = json.loads(row["project"]), json.loads(row["migration"])
        if p.get("updated_at", "") > link.get("updated_at", ""):
            continue  # The cached quote may describe an older setup.
        f = quote_features(p, link)
        if not f or type(link.get("estimated_cost")) is not int:
            continue
        if conn.execute("SELECT 1 FROM cost_quotes WHERE project_id=?", (p["id"],)).fetchone():
            continue
        key = "legacy:" + hashlib.sha256(json.dumps([p["id"], link["remote_project_id"], f["features_key"], link["estimated_cost"]]).encode()).hexdigest()
        observe_quote(conn, p, link, event_id=key, source="legacy_verified_record", observed_at=link.get("updated_at"))


def summary(conn, project, record, migration=None):
    # Remote metadata is slightly different after remuxing. Before a quote we
    # predict using the local precise duration; after verification use GGAB's.
    remote = quote_features(project, migration or {})
    duration = remote["duration_seconds"] if remote else (record or {}).get("duration")
    prediction = predict(conn, project, duration, migration)
    if not prediction:
        return None
    quotes = conn.execute("SELECT payload FROM cost_quotes WHERE project_id=? AND remote_id=? ORDER BY observed_at DESC,event_id DESC",
                          (project["id"], (migration or {}).get("remote_project_id"))).fetchall()
    latest = next((json.loads(r[0]) for r in quotes
                   if json.loads(r[0])["features"]["features_key"] == prediction["features"]["features_key"]), None)
    return {**prediction, "ggab_tokens": latest["ggab_tokens"] if latest else None,
            "ggab_observed_at": latest["observed_at"] if latest else None,
            "last_prediction_error": latest["prediction_error"] if latest else None}


def history(conn, pid):
    return {"quotes": [json.loads(r[0]) for r in conn.execute("SELECT payload FROM cost_quotes WHERE project_id=? ORDER BY observed_at DESC,event_id DESC LIMIT 100", (pid,))],
            "predictions": [json.loads(r[0]) for r in conn.execute("SELECT payload FROM cost_predictions WHERE project_id=? ORDER BY created_at DESC LIMIT 100", (pid,))]}
