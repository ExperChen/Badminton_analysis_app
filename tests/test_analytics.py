"""Check statistical meaning using hand-counted rallies and the observed reference."""

import csv
import hashlib
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as server
from analytics import analyze


def shot(rally, number, own, family, final=False, won=1, **extra):
    return {"match_id": "match", "rally_id": rally, "shot_id": f"{rally}-{number}",
            "game_number": 1, "rally_number": int(rally[-1]), "rally_number_in_game": int(rally[-1]),
            "shot_number_in_rally": number, "striker": "A1" if own else "B1",
            "striker_name": "Player A" if own else "Player B", "striker_is_you": int(own),
            "shot_family": family, "is_final_shot": int(final), "rally_won_by_you": won,
            "rally_duration_s": 10 if rally == "r1" else 20,
            "rally_end_type": "winner", "outcome": "winner" if final else "in_play",
            "score_a_before": 0, "score_b_before": 0, "score_a_after": 1, "score_b_after": 0,
            "video_timestamp_seconds": number * 2, "contact_point": None, **extra}


def test_rallies_not_weighted_by_their_shot_count():
    # Two rallies: A wins a three-shot rally, B wins a one-shot rally.
    rows = [shot("r1", 1, True, "Serve"), shot("r1", 2, False, "Lift"),
            shot("r1", 3, True, "Smash", final=True),
            shot("r2", 1, False, "Serve", final=True, won=0)]
    result = analyze(rows)
    assert result["summary"] == {"shots": 4, "own_shots": 2, "rallies": 2, "wins": 1,
        "losses": 1, "unknown": 0, "win_rate": 50.0, "avg_shots": 2, "avg_duration": 15}
    assert result["winning"][0]["name"] == "Smash"
    assert result["losing"][0]["name"] == "Serve"
    assert result["patterns"]["3"]["all"]["eligible"] == 1
    assert result["patterns"]["5"]["all"]["eligible"] == 0
    assert analyze(rows, "opponent")["winning"][0]["name"] == "Serve"


def test_opponent_error_is_attributed_to_previous_own_shot():
    rows = [shot("r1", 1, True, "Net"),
            shot("r1", 2, False, "Lift", final=True, outcome="unforced_error",
                 rally_end_type="unforced_error")]
    won = analyze(rows)["winning"]
    assert won == [{"name": "Net", "value": 1, "percent": 100.0,
                    "detail": {"Drew opp. unforced": 1}}]
    assert analyze(rows,"opponent")["losing"][0]["name"] == "Lift"


def test_unknown_and_incomplete_rallies_do_not_become_losses():
    rows = [shot("r1", 1, True, "Serve", won=None),
            shot("r2", 1, False, "Serve", final=True, won=None)]
    result = analyze(rows)
    assert result["summary"]["unknown"] == 2
    assert result["summary"]["win_rate"] is None
    assert result["winning"] == result["losing"] == []
    assert result["patterns"]["3"]["lost"]["eligible"] == 0
    assert analyze([])["summary"]["avg_duration"] is None


def test_reply_height_never_crosses_rally_or_missing_shots():
    rows = [shot("r1", 1, True, "Serve"),
            shot("r1", 2, False, "Net", contact_point="above_net_midline"),
            shot("r1", 4, True, "Kill", final=True),
            shot("r2", 1, False, "Net", final=True, won=0, contact_point="below_net")]
    result = analyze(rows)
    assert len(result["heights"]) == 3
    assert result["patterns"]["3"]["all"]["eligible"] == 0
    assert result["replies"] == [{"player":"Player A", "family":"Serve", "side":None,
        "direction":None, "variant":None, "rally_id":"r1", "shot_id":"r1-1", "time":2,
        "category":"above_net_midline"}]


def test_reused_rally_id_in_different_matches_stays_separate():
    rows=[shot("r1",1,True,"Serve",final=True),
          shot("r1",1,False,"Serve",final=True,won=0,match_id="other",shot_id="other-1")]
    assert analyze(rows)["summary"]["rallies"] == 2


@pytest.fixture
def sample_db(tmp_path, monkeypatch):
    target=tmp_path / "sample.db"
    with sqlite3.connect(target) as conn:
        # This schema runs ONLY against an isolated temporary test database.
        conn.executescript((server.ROOT / "schema.sql").read_text(encoding="utf-8"))
        rows=[shot("r1",1,True,"Serve",final=True,match_title="=unsafe",match_date="2026-01-01"),
              shot("r2",1,False,"Serve",final=True,won=0,game_number=2,match_title="=unsafe",match_date="2026-01-01")]
        for row in rows:
            keys=list(row)
            conn.execute(f"INSERT INTO shots ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", list(row.values()))
    monkeypatch.setattr(server,"DB_PATH",target)
    return target


def test_api_filters_validation_export_and_read_only(sample_db):
    before=hashlib.sha256(sample_db.read_bytes()).hexdigest()
    with TestClient(server.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/matches").json()[0]["rallies"] == 2
        d=client.get("/api/matches/match/breakdown?game=2&perspective=opponent").json()
        assert d["games"] == [1,2]
        assert d["summary"]["wins"] == 1
        assert d["summary"]["shots"] == 1
        assert client.get("/api/matches/match/breakdown?game=999").json()["summary"]["rallies"] == 0
        assert client.get("/api/matches/missing/breakdown").status_code == 404
        assert client.get("/api/matches/x%27%20OR%201%3D1/breakdown").status_code == 404
        assert client.get("/api/matches/match/breakdown?game=0").status_code == 422
        assert client.get("/api/matches/match/breakdown?perspective=invalid").status_code == 422
        response=client.get("/api/matches/match/export?game=2")
        assert response.status_code == 403
        assert 'text/csv' not in response.headers.get('content-type', '')
        assert client.get("/api/matches/match/export?game=999").status_code == 403
        assert client.get("/static/../ggab.db").status_code == 404
    assert hashlib.sha256(sample_db.read_bytes()).hexdigest() == before


def test_missing_database_does_not_create_a_file(tmp_path,monkeypatch):
    missing=tmp_path/"missing.db"
    monkeypatch.setattr(server,"DB_PATH",missing)
    with TestClient(server.app) as client:
        assert client.get("/api/matches").status_code == 503
    assert not missing.exists()


def test_empty_database(sample_db):
    with sqlite3.connect(sample_db) as conn:
        conn.execute("DELETE FROM shots")
    with TestClient(server.app) as client:
        assert client.get("/api/matches").json() == []
        assert client.get("/health").json() == {"status":"ok"}


def test_reference_match_matches_observed_website():
    # Independently observed on the reference page, 2026-09-06.
    rid="6b5209c2-e50c-4626-aa63-c43a4f2bd6e5"
    if not server.DB_PATH.exists():
        pytest.skip("Local reference database not available")
    rows=server.query("SELECT * FROM shots WHERE match_id=? ORDER BY rally_number,shot_number_in_rally", (rid,))
    if not rows:
        pytest.skip("Reference match not present")
    result=analyze(rows)
    assert result["summary"]["shots"] == 103
    assert result["summary"]["rallies"] == 23
    assert (result["summary"]["wins"],result["summary"]["losses"]) == (6,17)
    assert result["summary"]["avg_shots"] == 4.5
    assert result["summary"]["avg_duration"] == 7.5
    assert result["winning"][0]["percent"] == 50.0
    assert result["losing"][0]["percent"] == 29.4
    assert result["usage"][0]["percent"] == 32.7
    assert result["patterns"]["3"]["all"]["eligible"] == 18
    assert result["patterns"]["5"]["all"]["eligible"] == 11
    assert len([x for x in result["heights"] if x["player"]=="CHEN P. R."]) == 29
