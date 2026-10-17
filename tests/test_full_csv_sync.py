import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as server
import bridge
import full_csv_sync as sync
import video_tools as vt


REMOTE = 'd8a76ac5-2f3a-49ce-a27f-408c7e5d98e2'


@pytest.fixture
def store(tmp_path, monkeypatch):
    database = tmp_path / 'shots.db'
    with sqlite3.connect(database) as conn:
        conn.executescript((server.ROOT / 'schema.sql').read_text(encoding='utf-8'))
        conn.execute("INSERT INTO shots (match_id,rally_id,shot_id) VALUES ('old','r-old','s-old')")
        columns = [row[1] for row in conn.execute('PRAGMA table_info(shots)')]
    monkeypatch.setattr(server, 'DB_PATH', database)
    monkeypatch.setattr(sync, 'CSV_PATH', tmp_path / 'exports' / 'ggab-shots-latest.csv')

    def content(*shots):
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows({'match_id': REMOTE, 'rally_id':'r1', 'shot_id': shot,
                          'match_title':'Original.mp4', 'game_number':1,
                          'video_timestamp_seconds':2} for shot in shots)
        return output.getvalue()

    return database, content


def rows(database):
    with sqlite3.connect(database) as conn:
        return conn.execute('SELECT shot_id FROM shots ORDER BY shot_id').fetchall()


def test_full_snapshot_replaces_same_csv_and_database_with_recoverable_backups(store):
    database, content = store
    ticket = sync.begin(database, REMOTE)['ticket']
    first = sync.apply_snapshot(database, ticket, content('s1','s2'), {})
    assert rows(database) == [('s1',), ('s2',)]
    assert rows(first['backup']) == [('s-old',)]
    first_bytes = sync.CSV_PATH.read_bytes()
    second = sync.apply_snapshot(database, sync.begin(database, REMOTE, force=True)['ticket'], content('s3'), {})
    assert first['path'] == second['path']
    assert rows(database) == [('s3',)]
    assert sync.CSV_PATH.read_bytes() != first_bytes
    saved = sync.status(database)
    assert Path(saved['csv_backup']).read_bytes() == first_bytes
    assert saved['rows'] == 1 and saved['matches'] == 1
    assert 'ticket' not in saved


@pytest.mark.parametrize('bad', ['empty','duplicate','truncated','wrong_match','html'])
def test_invalid_snapshot_never_replaces_current_csv_or_shots(store, bad):
    database, content = store
    sync.apply_snapshot(database, sync.begin(database, REMOTE)['ticket'], content('s1'), {})
    before = sync.CSV_PATH.read_bytes()
    text = {'empty':content(), 'duplicate':content('s2','s2'),
            'truncated':content('s2').rsplit(',',1)[0],
            'wrong_match':content('s2').replace(REMOTE,'different'),
            'html':'<html>sign in</html>'}[bad]
    with pytest.raises((ValueError,csv.Error)):
        sync.apply_snapshot(database, sync.begin(database, REMOTE, force=True)['ticket'], text, {})
    assert rows(database) == [('s1',)]
    assert sync.CSV_PATH.read_bytes() == before


def test_file_failure_rolls_back_database(store, monkeypatch):
    database, content = store
    sync.apply_snapshot(database, sync.begin(database, REMOTE)['ticket'], content('s1'), {})
    before = sync.CSV_PATH.read_bytes()
    def fail_write(*args):
        raise OSError('file busy')
    monkeypatch.setattr(sync,'atomic_write',fail_write)
    with pytest.raises(OSError,match='file busy'):
        sync.apply_snapshot(database, sync.begin(database, REMOTE, force=True)['ticket'], content('s2'), {})
    assert rows(database) == [('s1',)]
    assert sync.CSV_PATH.read_bytes() == before


def test_late_ticket_cannot_overwrite_newer_export_and_unchanged_csv_does_not_repeat_backup(store):
    database, content = store
    ticket1 = sync.begin(database,REMOTE)['ticket']
    assert sync.begin(database,REMOTE) == {'skipped':True}
    sync.fail(database,ticket1,'network unavailable')
    ticket2 = sync.begin(database,REMOTE)['ticket']
    with pytest.raises(ValueError,match='过期'):
        sync.apply_snapshot(database,ticket1,content('old-result'),{})
    first = sync.apply_snapshot(database,ticket2,content('s1'),{})
    assert sync.begin(database,REMOTE) == {'skipped':True}
    second = sync.apply_snapshot(database,sync.begin(database,REMOTE,force=True)['ticket'],content('s1'),{})
    assert not second['changed']
    assert second['backup'] == first['backup']
    assert second['revision'] == first['revision']
    with sqlite3.connect(database) as conn:
        conn.execute('DELETE FROM shots')
    repaired = sync.apply_snapshot(database,sync.begin(database,REMOTE,force=True)['ticket'],content('s1'),{})
    assert repaired['changed'] and rows(database) == [('s1',)]


def test_full_sync_api_reuses_original_video_and_updates_local_breakdown(store, tmp_path):
    database, content = store
    original = tmp_path/'Original.mp4'
    original.write_bytes(b'existing original')
    record = vt.register('video-original',original,'Original.mp4',{'duration':10,'has_audio':False})
    with bridge.db() as conn:
        conn.execute('INSERT INTO projects VALUES (?,?,?)',
                     ('local',json.dumps({'session_id':'video-original'}),'now'))
        conn.execute('INSERT INTO migration_links VALUES (?,?,?)',
                     ('local',json.dumps({'remote_project_id':REMOTE,'status':'finalized',
                                         'association':{'verified':True}}),'now'))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',('local','complete','{}',0,'now'))
    client = TestClient(server.app)
    token = client.post('/api/bridge/pair',headers={'X-GGAB-Local':'1'}).json()['token']
    auth = {'Authorization':'Bearer '+token}
    job = client.post('/api/bridge/worker/claim',headers=auth).json()['job']
    assert job['needs_export']
    start = client.post('/api/bridge/worker/projects/local/full-export/start',headers=auth,
                        json={'remote_project_id':REMOTE}).json()
    result = client.post('/api/bridge/worker/full-export/'+start['ticket'],headers=auth,
                         json={'csv_text':content('s1','s2')})
    assert result.status_code == 200, result.text
    assert result.json()['ok']
    matches = client.get('/api/matches').json()
    assert len(matches) == 1 and matches[0]['match_id'] == REMOTE
    assert matches[0]['has_video'] and matches[0]['local_session_id'] == 'video-original'
    assert client.get('/api/matches/video-original/breakdown').json()['summary']['shots'] == 2
    assert client.get('/api/matches/'+REMOTE+'/video').content == b'existing original'
    assert server.target_video_session('Original.mp4',REMOTE)[0] == 'video-original'
    assert vt.registry() == {'video-original':record}
    assert client.get('/api/bridge/full-export.csv').status_code == 403
    assert sync.CSV_PATH.read_bytes() == content('s1','s2').encode()
    assert client.get('/api/data-revision').json()['revision'] == result.json()['revision']
    assert client.get('/api/bridge/status').json()['full_export']['state'] == 'complete'
