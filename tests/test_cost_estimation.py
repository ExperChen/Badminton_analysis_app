import json
import math
import sqlite3
import time

import pytest
import cost_estimation as cost
import tracker
import bridge
import video_tools as vt
import app as server
from fastapi.testclient import TestClient


def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cost.ensure_schema(conn)
    return conn


def project(pid="p", segments=None, game_type="singles"):
    return {"id": pid, "session_id": "video-" + pid, "title": pid, "game_type": game_type,
            "segments": segments or [], "snapshots": {"A1": {"video_version": "original-v1"}}}


def link(seconds, tokens, remote="remote"):
    return {"remote_project_id": remote, "estimated_cost": tokens,
            "association": {"verified": True, "remote_video": {"duration": seconds},
                            "local_video": {"fingerprint": {"value": "sha256-original"}}}}


def observe(conn, pid, seconds, tokens, date, game_type="singles"):
    cost.observe_quote(conn, project(pid, game_type=game_type), link(seconds, tokens, pid),
                       event_id="event-"+pid+"-"+str(tokens), source="ggab_api", observed_at=date)


def test_baseline_reproduces_seven_historical_quotes_with_exact_segment_duration():
    conn = connection()
    for duration, expected in [(294.755,1638),(293.919,1633),(183.717,1021),
                               (241.419,1342),(244.623,1360),(283.4128,1575)]:
        assert cost.predict(conn, project(), duration)["tokens"] == expected
    p = project(segments=[{"start":37,"end":334},{"start":442,"end":934.985867}])
    assert cost.predict(conn,p,934.985867)["tokens"] == 4389
    assert cost.predict(conn,p,934.985867)["features"]["effective_seconds"] == pytest.approx(789.985867)


@pytest.mark.parametrize("duration,segments", [(None,[]),(0,[]),(True,[]),(float("nan"),[]),
    (10,[{"start":0,"end":11}]),(10,[{"start":4,"end":3}]),
    (10,[{"start":0,"end":5},{"start":4,"end":6}]),(10,[{}])])
def test_unknown_duration_and_invalid_segments_do_not_invent_cost(duration, segments):
    assert cost.predict(connection(),project(segments=segments),duration) is None


def test_live_quote_preserves_prequote_prediction_despite_remote_remux_rounding():
    conn = connection()
    before = cost.predict(conn, project("deng"), 405.004933)
    cost.observe_quote(conn, project("deng"), link(405.0064,2588,"deng"),
                       event_id="job-1",source="ggab_api")
    history = cost.history(conn,"deng")
    assert history["quotes"][0]["prediction_tokens"] == before["tokens"] == 2251
    assert history["quotes"][0]["prediction_error"] == -337
    assert history["quotes"][0]["prediction_model"] == before["model"]["version"]
    after = cost.summary(conn,project("deng"),{"duration":405.004933},link(405.0064,2588,"deng"))
    assert after["tokens"] == after["ggab_tokens"] == 2588
    assert after["model"]["confidence"] == "tentative"
    assert after["authoritative"] is False


def test_recent_regime_drift_uses_distinct_projects_and_does_not_claim_single_sample_certainty():
    conn = connection()
    for i, seconds in enumerate([183.717,241.419,283.4128]):
        observe(conn,"old"+str(i),seconds,math.ceil(seconds*cost.BASE_RATE),"2026-09-27T00:00:0"+str(i))
    assert cost.model(conn,"singles")["confidence"] == "historical_match"
    observe(conn,"deng",405.0064,2588,"2026-10-04T01:00:00")
    assert cost.model(conn,"singles")["sample_count"] == 1
    assert cost.model(conn,"singles")["multiplier"] == pytest.approx(1.15)
    observe(conn,"deng",405.0064,2589,"2026-10-04T02:00:00")
    assert cost.model(conn,"singles")["sample_count"] == 1
    for i,seconds in enumerate([200,300]):
        observe(conn,"new"+str(i),seconds,math.ceil(seconds*cost.BASE_RATE*1.15),"2026-10-04T03:00:0"+str(i))
    assert cost.model(conn,"singles")["sample_count"] == 2
    assert cost.model(conn,"doubles")["sample_count"] == 0


def test_prediction_deduplication_and_new_setup_does_not_reuse_old_quote():
    conn = connection()
    p = project()
    first = cost.predict(conn,p,100)
    assert cost.predict(conn,p,100)["id"] == first["id"]
    assert len(cost.history(conn,"p")["predictions"]) == 1
    cost.observe_quote(conn,p,link(100,556),event_id="job-1",source="ggab_api")
    cost.observe_quote(conn,p,link(100,556),event_id="job-1",source="ggab_api")
    assert len(cost.history(conn,"p")["quotes"]) == 1
    changed = project(segments=[{"start":10,"end":20}])
    result = cost.summary(conn,changed,{"duration":100},link(100,556))
    assert result["ggab_tokens"] is None
    assert result["features"]["effective_seconds"] == 10
    assert result["id"] != first["id"]
    # A replacement remote project on the same original must get its own quote.
    replacement = cost.summary(conn,p,{"duration":100},link(100,556,"new-remote"))
    assert replacement["ggab_tokens"] is None


def test_backfill_is_once_only_and_never_invents_historical_forecasts():
    with tracker.database() as conn:
        p = {**project(),"updated_at":"2026-09-27"}
        m = {**link(100,556),"updated_at":"2026-09-28"}
        conn.execute("INSERT INTO projects VALUES (?,?,?)",("p",json.dumps(p),p["updated_at"]))
        conn.execute("INSERT INTO migration_links VALUES (?,?,?)",("p",json.dumps(m),m["updated_at"]))
        cost.predict(conn,p,100)
        cost.backfill(conn);cost.backfill(conn)
        q = cost.history(conn,"p")["quotes"]
        assert len(q) == 1 and q[0]["source"] == "legacy_verified_record"
        assert q[0]["prediction_tokens"] is None and q[0]["prediction_error"] is None
        assert q[0]["baseline_tokens"] == 556


def test_cost_api_records_forecasts_without_creating_or_confirming_remote_jobs():
    vt.save_json(vt.MEDIA_ROOT/"sessions.json",{"video-p":{"duration":283.411878}})
    with tracker.database() as conn:
        conn.execute("INSERT INTO projects VALUES (?,?,?)",("p",json.dumps(project()),"now"))
    client=TestClient(server.app)
    result=client.get("/api/tracker/projects/p/cost-history")
    assert result.status_code == 200
    assert result.json()["current"]["tokens"] == 1575
    assert result.json()["quotes"] == []
    assert len(result.json()["predictions"]) == 1
    assert client.get("/api/tracker/projects").json()[0]["cost_prediction"]["authoritative"] is False
    with tracker.database() as conn:
        assert not conn.execute("SELECT * FROM migration_workflows").fetchall()
        assert not conn.execute("SELECT * FROM migration_links").fetchall()


def test_worker_estimate_records_live_comparison_in_same_transaction():
    client=TestClient(server.app)
    p=project("p")
    original=link(405.0064,2251,"remote-p")
    original.update(status="review",source="uploaded")
    with bridge.db() as conn:
        conn.execute("INSERT INTO projects VALUES (?,?,?)",("p",json.dumps(p),"now"))
        conn.execute("INSERT INTO migration_links VALUES (?,?,?)",("p",json.dumps(original),"now"))
        conn.execute("INSERT INTO migration_workflows VALUES (?,?,?,?,?)",("p","estimate",json.dumps({"remote_project_id":"remote-p"}),0,"now"))
        stamp=time.time()
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) VALUES ('estimate','p','remote-p','running',?,?, 'lease','migrate')",(stamp,stamp+600))
        cost.predict(conn,p,405.004933)
    token=client.post("/api/bridge/pair",headers={"X-GGAB-Local":"1"}).json()["token"]
    auth={"Authorization":"Bearer "+token}
    request={"lease":"lease","progress":{"stage":"await_confirmation","data":{"estimated_cost":2588,"balance_before":9999}}}
    assert client.post("/api/bridge/worker/jobs/estimate/result",headers=auth,json=request).status_code==200
    assert client.post("/api/bridge/worker/jobs/estimate/result",headers=auth,json=request).status_code==409
    history=client.get("/api/tracker/projects/p/cost-history").json()
    assert len(history["quotes"])==1
    assert history["quotes"][0]["source"]=="ggab_api"
    assert history["quotes"][0]["prediction_error"]==-337
    status=client.get("/api/bridge/status").json()["workflows"][0]
    assert status["estimated_cost"]==2588 and status["stage"]=="await_confirmation"
    assert status["cost_prediction"]["authoritative"] is False
    # Merely learning must not submit tracking or use the forecast as fee authority.
    assert client.post("/api/bridge/confirm/p",headers={"X-GGAB-Local":"1"},json={"remote_project_id":"remote-p","estimated_cost":2251}).status_code==409
