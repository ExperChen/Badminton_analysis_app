"""The standalone app exposes local resources, never a remote executor or billing API."""
import sys
import sqlite3
import pytest
from fastapi.testclient import TestClient
import app as server
import tracker
import local_data
from test_accounts import environment, post

@pytest.mark.parametrize("url", ["/api/my/export.csv", "/api/matches/A/export"])
def test_csv_download_stays_disabled(environment,url):
    for client in environment["clients"][:3]:
        assert client.get(url).status_code == 403

@pytest.mark.parametrize("url", ["/api/bridge/status", "/api/bridge/worker/claim", "/api/tokens/me",
                                  "/api/admin/tokens", "/account/tokens",
                                  "/api/tracker/projects/project-A/handoff",
                                  "/api/tracker/projects/project-A/cost-history",
                                  "/api/tracker/projects/project-A/migration"])
def test_removed_endpoints_do_not_exist(environment,url):
    admin=environment["clients"][0]
    assert admin.get(url).status_code == 404

def test_local_runtime_has_no_remote_modules():
    assert not {"bridge","cost_estimation","token_wallet","full_csv_sync"} & sys.modules.keys()

def test_tracker_database_does_not_create_remote_jobs():
    with tracker.database() as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects","snapshots","saved_player_names"} <= tables
    assert not {"migration_links","migration_workflows","token_holds","cost_quotes"} & tables

def test_local_bindings_and_revision_remain_read_only(environment):
    database=environment["db"]
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE match_video_links(match_id TEXT PRIMARY KEY,session_id TEXT,project_id TEXT)")
        conn.execute("INSERT INTO match_video_links VALUES ('A','video-A','project-A')")
        before=conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    assert local_data.aliases(database)=={"A":"video-A"}
    assert local_data.project_matches(database)=={"A":"project-A"}
    alice,bob=environment["clients"][1:3]
    assert next(p for p in alice.get("/api/tracker/projects").json() if p["id"]=="project-A")["analysis_match_id"]=="A"
    assert bob.get("/api/matches/A/video").status_code==404
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0]==before
