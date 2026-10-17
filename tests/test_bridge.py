import json
import time
import pytest

from fastapi.testclient import TestClient
import app as server
import bridge

LOCAL = {'X-GGAB-Local': '1'}
REMOTE = '55f9891e-86b0-41a3-bd1c-e6b4ee629606'
VIDEO = '0f720448-aaf7-4213-ba04-32d58f1ef58c'


def video_check_client():
    client, auth = setup_client('finalized')
    with bridge.db() as conn:
        row = conn.execute('SELECT payload FROM migration_links').fetchone()
        link = json.loads(row[0])
        link['association']['remote_video'] = {'remote_video_id': VIDEO,
            'filename': 'original.mp4', 'size_bytes': 123, 'duration': 10}
        conn.execute('UPDATE migration_links SET payload=?', (json.dumps(link),))
    return client, auth


def test_manual_video_check_is_read_only_deduplicated_and_version_gated():
    client, auth = video_check_client()
    url = '/api/bridge/projects/local/check-video'
    assert client.post(url).status_code == 403
    first = client.post(url, headers=LOCAL).json()
    assert client.post(url, headers=LOCAL).json()['job_id'] == first['job_id']
    old = client.post('/api/bridge/worker/claim', headers=auth, json={'extension_version':'1.4.1'}).json()['job']
    assert not old or old['kind'] != 'check_video'
    job = client.post('/api/bridge/worker/claim', headers=auth, json={'extension_version':'1.4.3'}).json()['job']
    assert job['kind'] == 'check_video' and job['video']['video_id'] == VIDEO
    path = '/api/bridge/worker/jobs/' + job['id'] + '/result'
    bad = {'video_id':'another', 'status':'missing','reason':'missing', 'rule_version':2}
    assert client.post(path, headers=auth,json={'lease':job['lease'],'video_check':bad}).status_code == 409
    good = {**bad, 'video_id':VIDEO}
    assert client.post(path,headers=auth,json={'lease':job['lease'],'video_check':good}).json()['ok']
    status = client.get('/api/bridge/status').json()
    assert status['video_checks']['local']['status'] == 'missing'
    with bridge.db() as conn:
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'finalized'
        assert not conn.execute('SELECT * FROM migration_workflows').fetchall()


def test_unknown_video_check_does_not_claim_upload_failed_or_change_tracking():
    client, auth = video_check_client()
    client.post('/api/bridge/projects/local/check-video',headers=LOCAL)
    job = client.post('/api/bridge/worker/claim',headers=auth,json={'extension_version':'1.4.3'}).json()['job']
    client.post('/api/bridge/worker/jobs/'+job['id']+'/result',headers=auth,json={
        'lease':job['lease'],'video_check':{'video_id':VIDEO,'status':'unknown','reason':'login expired','rule_version':2}})
    assert client.get('/api/bridge/status').json()['video_checks']['local']['status'] == 'unknown'


def test_video_check_requires_video_id_not_title():
    client, _ = setup_client()
    assert client.post('/api/bridge/projects/local/check-video', headers=LOCAL).status_code == 409


def test_periodic_status_sync_stores_video_failure_without_rolling_back_finalized():
    client, auth = video_check_client()
    job = client.post('/api/bridge/worker/claim', headers=auth,json={'extension_version':'1.4.2'}).json()['job']
    assert job['kind'] == 'sync_status' and job['video']['video_id'] == VIDEO
    result = client.post('/api/bridge/worker/jobs/'+job['id']+'/result',headers=auth,json={
        'lease':job['lease'],'status':'finalized','video_check':{
            'video_id':VIDEO,'status':'missing','reason':'library record missing','rule_version':2}})
    assert result.json()['ok']
    assert client.get('/api/bridge/status').json()['video_checks']['local']['status'] == 'missing'
    with bridge.db() as conn:
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'finalized'


def test_known_missing_source_blocks_fee_confirmation_and_old_check_is_not_reused():
    client, auth = video_check_client()
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])
        link['status']='review'
        conn.execute('UPDATE migration_links SET payload=?',(json.dumps(link),))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',('local','await_confirmation',
            json.dumps({'remote_project_id':REMOTE,'estimated_cost':385}),0,'now'))
        conn.execute('INSERT INTO video_checks VALUES (?,?,?)',('local',json.dumps({
            'video_id':VIDEO,'status':'missing','reason':'missing','checked_at':'now'}),'now'))
    assert not client.get('/api/bridge/status').json()['workflows'][0]['confirmation_ready']
    assert client.post('/api/bridge/confirm/local',headers=LOCAL,json={
        'remote_project_id':REMOTE,'estimated_cost':385}).status_code == 409
    with bridge.db() as conn:
        link['association']['remote_video']['remote_video_id']='another-video'
        conn.execute('UPDATE migration_links SET payload=?',(json.dumps(link),))
    assert 'local' not in client.get('/api/bridge/status').json()['video_checks']


def test_video_target_binds_only_the_registered_original_for_the_exact_video_id():
    client, _ = video_check_client()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',('local','complete',
            json.dumps({'video_id':VIDEO,'storage_path':'owner/original.mp4'}),0,'now'))
        assert bridge.video_target(conn,'local')['storage_path'] == 'owner/original.mp4'
        conn.execute('UPDATE migration_workflows SET payload=?',
            (json.dumps({'storage_path':'owner/unbound.mp4'}),))
        assert bridge.video_target(conn,'local')['storage_path'] is None


def test_old_detector_cannot_overwrite_corrected_check_and_observed_values_are_saved():
    client, auth = video_check_client()
    url='/api/bridge/projects/local/check-video'
    client.post(url,headers=LOCAL)
    old=client.post('/api/bridge/worker/claim',headers=auth,json={'extension_version':'1.4.2'}).json()['job']
    assert not old or old['kind'] != 'check_video'
    job=client.post('/api/bridge/worker/claim',headers=auth,json={'extension_version':'1.4.3'}).json()['job']
    path='/api/bridge/worker/jobs/'+job['id']+'/result'
    check={'video_id':VIDEO,'status':'ready','reason':'original storage verified','rule_version':2,
        'observed':{'filename':'renamed.mp4','metadata_size_bytes':80,'original_size_bytes':123,'duration':10}}
    assert client.post(path,headers=auth,json={'lease':job['lease'],'video_check':check}).json()['ok']
    assert client.get('/api/bridge/status').json()['video_checks']['local']['observed']['original_size_bytes']==123
    client.post(url,headers=LOCAL)
    job=client.post('/api/bridge/worker/claim',headers=auth,json={'extension_version':'1.4.3'}).json()['job']
    assert client.post('/api/bridge/worker/jobs/'+job['id']+'/result',headers=auth,json={
        'lease':job['lease'],'video_check':{'video_id':VIDEO,'status':'mismatch','reason':'legacy detector'}}).json()['ok']
    assert client.get('/api/bridge/status').json()['video_checks']['local']['status']=='ready'


@pytest.mark.parametrize('stage,delta,error,immediate', [
    ('upload_video', {'upload_offset':25165824}, None, True),
    ('upload_video', {'upload_offset':12582912}, None, False),
    ('upload_video', {}, 'network failure', False),
    ('wait_video', {}, None, False),
])
def test_chunk_ack_is_immediately_claimable_but_polling_and_errors_back_off(stage, delta, error, immediate):
    client, auth = setup_client()
    now = time.time()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local',stage,json.dumps({'upload_offset':12582912}),0,'now'))
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) "
                     "VALUES ('upload','local',?,'running',?,?,'lease','migrate')", (REMOTE,now,now+600))
    body = {'lease':'lease','error':error} if error else {'lease':'lease','progress':{'stage':stage,'data':delta}}
    response = client.post('/api/bridge/worker/jobs/upload/result',headers=auth,json=body)
    assert response.status_code == 200
    with bridge.db() as conn:
        next_run = conn.execute('SELECT next_run FROM migration_workflows').fetchone()[0]
    assert (next_run <= time.time()) is immediate
    if not immediate:
        assert next_run > now + 50


def setup_client(status='review'):
    client = TestClient(server.app)
    with bridge.db() as conn:
        conn.execute('INSERT INTO projects VALUES (?,?,?)', ('local', '{}', 'now'))
        link = {'remote_project_id': REMOTE, 'status': status, 'source': 'uploaded', 'estimated_cost': 385,
                'association': {'verified': True}}
        conn.execute('INSERT INTO migration_links VALUES (?,?,?)', ('local', json.dumps(link), 'now'))
    token = client.post('/api/bridge/pair', headers=LOCAL).json()['token']
    return client, {'Authorization': 'Bearer ' + token}


def test_pair_security_and_scoped_authorization():
    client = TestClient(server.app)
    assert client.post('/api/bridge/pair').status_code == 403
    assert client.post('/api/bridge/pair', headers={**LOCAL, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/bridge/worker/claim').status_code == 401
    client, auth = setup_client()
    assert client.post('/api/tracker/projects', headers=auth, json={}).status_code == 403
    client.post('/api/bridge/pair', headers=LOCAL)
    assert client.post('/api/bridge/worker/claim', headers=auth).status_code == 401


def test_claim_dedupe_result_and_preserve_cost():
    client, auth = setup_client()
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    assert job['kind'] == 'sync_status'
    assert client.post('/api/bridge/worker/claim', headers=auth).json()['job'] is None
    path = f"/api/bridge/worker/jobs/{job['id']}/result"
    body = {'lease': job['lease'], 'status': 'finalized'}
    assert client.post(path, headers=auth, json={**body, 'lease': 'wrong'}).status_code == 409
    assert client.post(path, headers=auth, json=body).json()['ok']
    assert client.post(path, headers=auth, json=body).status_code == 409
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])
    assert link['status'] == 'finalized' and link['estimated_cost'] == 385 and link['source'] == 'uploaded'
    assert client.get('/api/bridge/status').json()['connected']


def test_lease_recovery_and_no_rollback():
    client, auth = setup_client('finalized')
    old = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    with bridge.db() as conn:
        conn.execute('UPDATE bridge_jobs SET lease_until=?', (time.time() - 1,))
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    assert job['id'] == old['id'] and job['lease'] != old['lease']
    path = f"/api/bridge/worker/jobs/{job['id']}/result"
    assert client.post(path, headers=auth, json={'lease':old['lease'], 'status':'processing'}).status_code == 409
    assert not client.post(path, headers=auth, json={'lease':job['lease'], 'status':'processing'}).json()['ok']
    with bridge.db() as conn:
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'finalized'


def test_changed_link_and_error_do_not_overwrite():
    client, auth = setup_client()
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    with bridge.db() as conn:
        conn.execute('UPDATE migration_links SET payload=?', (json.dumps({'remote_project_id':'new', 'status':'review'}),))
    path = f"/api/bridge/worker/jobs/{job['id']}/result"
    result = client.post(path, headers=auth, json={'lease':job['lease'], 'status':'finalized'}).json()
    assert not result['ok']
    assert client.get('/api/bridge/status').json()['jobs'][0]['state'] == 'failed'


def test_failed_read_preserves_migration():
    client, auth = setup_client()
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    path = f"/api/bridge/worker/jobs/{job['id']}/result"
    assert client.post(path, headers=auth, json={'lease':job['lease'], 'status':'review', 'error':'bad'}).status_code == 422
    assert not client.post(path, headers=auth, json={'lease':job['lease'], 'error':'GGAB 登录已失效'}).json()['ok']
    with bridge.db() as conn:
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'review'


def test_ambiguous_association_is_not_claimed():
    client, auth = setup_client()
    with bridge.db() as conn:
        conn.execute('INSERT INTO projects VALUES (?,?,?)', ('duplicate', '{}', 'now'))
        conn.execute('INSERT INTO migration_links SELECT ?,payload,updated_at FROM migration_links WHERE project_id=?', ('duplicate','local'))
    assert client.post('/api/bridge/worker/claim', headers=auth).json()['job'] is None


def test_unverified_legacy_association_gets_read_only_inspection_job():
    client, auth = setup_client()
    with bridge.db() as conn:
        row = conn.execute('SELECT payload FROM migration_links WHERE project_id=?', ('local',)).fetchone()
        link = json.loads(row[0]); link.pop('association')
        conn.execute('UPDATE migration_links SET payload=? WHERE project_id=?', (json.dumps(link), 'local'))
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    assert job['kind'] == 'inspect_remote'
    path = f"/api/bridge/worker/jobs/{job['id']}/result"
    result = client.post(path, headers=auth, json={'lease':job['lease'], 'error':'只读诊断',
        'diagnostic':{'elements':[{'tag':'h1','text':'Setup'}]}})
    assert result.status_code == 200
    stored = client.get('/api/bridge/status').json()['jobs'][0]
    assert stored['diagnostic']['elements'][0]['tag'] == 'h1'


def test_per_project_cost_confirmation_requires_exact_remote_and_cost():
    client, _ = setup_client()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'await_confirmation', json.dumps({'remote_project_id': REMOTE,
                       'estimated_cost': 385}), 0, 'now'))
    url = '/api/bridge/confirm/local'
    assert client.post(url, json={'remote_project_id':REMOTE, 'estimated_cost':385}).status_code == 403
    assert client.post(url, headers=LOCAL, json={'remote_project_id':REMOTE, 'estimated_cost':384}).status_code == 409
    assert client.post(url, headers=LOCAL, json={'remote_project_id':'wrong', 'estimated_cost':385}).status_code == 409
    assert client.post(url, headers=LOCAL, json={'remote_project_id':REMOTE, 'estimated_cost':385}).json()['queued']
    assert client.post(url, headers=LOCAL, json={'remote_project_id':REMOTE, 'estimated_cost':385}).status_code == 409
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'confirm_tracking'
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'review'


def test_ggab_reestimate_corrects_local_cost_without_submitting_or_reusing_old_consent():
    client, auth = setup_client()
    now = time.time()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'confirm_tracking', json.dumps({'remote_project_id': REMOTE,
                       'estimated_cost': 385}), 0, 'now'))
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) "
                     "VALUES ('reestimate','local',?,'running',?,?,'lease','migrate')",
                     (REMOTE, now, now + 600))
    correction = {'lease':'lease', 'progress':{'stage':'await_confirmation', 'data':{
        'estimated_cost':500, 'balance_before':1200, 'estimate_changed_from':385}}}
    result = client.post('/api/bridge/worker/jobs/reestimate/result', headers=auth, json=correction)
    assert result.status_code == 200 and result.json() == {'ok':True, 'error':None, 'stage':'await_confirmation'}
    flow = client.get('/api/bridge/status').json()['workflows'][0]
    assert flow['estimated_cost'] == 500 and flow['estimate_changed_from'] == 385
    assert flow['balance_before'] == 1200 and flow['confirmation_ready']
    assert flow['required_extension_version'] == '1.5.0'
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])
        assert link['estimated_cost'] == 500 and link['status'] == 'review'
    url = '/api/bridge/confirm/local'
    assert client.post(url, headers=LOCAL,
                       json={'remote_project_id':REMOTE, 'estimated_cost':385}).status_code == 409
    assert client.post(url, headers=LOCAL,
                       json={'remote_project_id':REMOTE, 'estimated_cost':500}).json()['queued']


def test_ggab_reestimate_rejects_invalid_delta_and_old_extension():
    assert bridge.required_version('confirm_tracking', {}) == '1.5.0'
    assert bridge.required_version('await_confirmation', {'required_extension_version':'1.3.3'}) == '1.5.0'
    assert bridge.required_version('confirm_tracking', {'required_extension_version':'1.5.0'}) == '1.5.0'
    assert not bridge.extension_supports_version('1.4.0', bridge.required_version('confirm_tracking', {}))
    client, auth = setup_client()
    now = time.time()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'confirm_tracking', json.dumps({'remote_project_id':REMOTE,
                       'estimated_cost':385}), 0, 'now'))
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) "
                     "VALUES ('bad-delta','local',?,'running',?,?,'lease','migrate')",
                     (REMOTE, now, now + 600))
    response = client.post('/api/bridge/worker/jobs/bad-delta/result', headers=auth,
                           json={'lease':'lease', 'progress':{'stage':'await_confirmation', 'data':{
                               'estimated_cost':500, 'estimate_changed_from':999}}})
    assert response.status_code == 422
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'confirm_tracking'
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['estimated_cost'] == 385


def test_old_extension_cannot_claim_fee_submission(monkeypatch, tmp_path):
    client, auth = setup_client()
    import video_tools as vt
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(vt, 'source', lambda session: ({'filename':'original.mp4','duration':10}, source))
    data = {'remote_project_id':REMOTE, 'estimated_cost':385,
            'source_path':str(source.resolve()), 'source_size':source.stat().st_size,
            'source_mtime_ns':source.stat().st_mtime_ns, 'project_updated_at':'now'}
    with bridge.db() as conn:
        conn.execute('UPDATE projects SET payload=? WHERE id=?',
                     (json.dumps({'session_id':'source','updated_at':'now'}), 'local'))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'confirm_tracking', json.dumps(data), 0, 'now'))
    old = client.post('/api/bridge/worker/claim', headers=auth,
                      json={'extension_version':'1.4.0'}).json()
    assert old['job'] is None
    fresh = client.post('/api/bridge/worker/claim', headers=auth,
                        json={'extension_version':'1.5.0'}).json()['job']
    assert fresh['kind'] == 'migrate' and fresh['stage'] == 'confirm_tracking'


@pytest.mark.parametrize('change', ['workflow_cost', 'workflow_remote', 'unverified', 'already_processing', 'already_finalized'])
def test_confirmation_rejects_stale_or_unverified_workflow_and_preserves_state(change):
    client, _ = setup_client()
    data = {'remote_project_id': REMOTE, 'estimated_cost': 385}
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])
        if change == 'workflow_cost':
            data['estimated_cost'] = 999
        elif change == 'workflow_remote':
            data['remote_project_id'] = 'other'
        elif change == 'unverified':
            link['association']['verified'] = False
        else:
            link['status'] = 'processing' if change == 'already_processing' else 'finalized'
        conn.execute('UPDATE migration_links SET payload=?', (json.dumps(link),))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'await_confirmation', json.dumps(data), 0, 'now'))
    assert not client.get('/api/bridge/status').json()['workflows'][0]['confirmation_ready']
    result = client.post('/api/bridge/confirm/local', headers=LOCAL,
                         json={'remote_project_id': REMOTE, 'estimated_cost': 385})
    assert result.status_code == 409
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'await_confirmation'
        assert conn.execute('SELECT count(*) FROM bridge_jobs').fetchone()[0] == 0


def test_confirmation_status_is_ready_only_for_exact_verified_unsubmitted_workflow():
    client, _ = setup_client()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'await_confirmation', json.dumps({'remote_project_id': REMOTE,
                       'estimated_cost': 385}), 0, 'now'))
    assert client.get('/api/bridge/status').json()['workflows'][0]['confirmation_ready']
    assert client.post('/api/bridge/confirm/local', headers=LOCAL,
                       json={'remote_project_id': REMOTE, 'estimated_cost': 385.5}).status_code == 422
    client.post('/api/bridge/confirm/local', headers=LOCAL,
                json={'remote_project_id': REMOTE, 'estimated_cost': 385})
    assert not client.get('/api/bridge/status').json()['workflows'][0]['confirmation_ready']


def test_empty_shot_export_is_rejected_before_csv_creation():
    client, auth = setup_client('finalized')
    result = client.post('/api/bridge/worker/projects/local/shots-export', headers=auth,
                         json={'remote_project_id':REMOTE, 'shots':[], 'rallies':[]})
    assert result.status_code == 409


def test_status_exposes_confirmed_video_upload_progress_and_worker_version():
    client, auth = setup_client()
    # A normal old association still produces a read-only job; the version is
    # recorded separately from the task result.
    client.post('/api/bridge/worker/claim', headers=auth, json={'extension_version':'1.3.0'})
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local', 'upload_video', json.dumps({'source_path':'test-media/original.mp4',
                       'source_size':20971520, 'upload_offset':10485760}), 0, 'now'))
    status = client.get('/api/bridge/status').json()
    assert status['workflows'][0]['source_size'] == 20971520
    assert status['workflows'][0]['upload_offset'] == 10485760
    assert status['workflows'][0]['video_filename'] == 'original.mp4'
    assert client.get('/api/bridge/status').json()['extension_version'] == '1.3.0'


def test_unverified_project_observation_does_not_change_migration():
    client, auth = setup_client('review')
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links WHERE project_id=?', ('local',)).fetchone()[0])
        link.pop('association')
        conn.execute('UPDATE migration_links SET payload=? WHERE project_id=?', (json.dumps(link), 'local'))
    job = client.post('/api/bridge/worker/claim', headers=auth).json()['job']
    result = client.post(f"/api/bridge/worker/jobs/{job['id']}/result", headers=auth,
                         json={'lease':job['lease'], 'status':'finalized',
                               'diagnostic':{'source':'ggab_project_api', 'remote_status':'finalized'}})
    assert result.json()['ok']
    stored = client.get('/api/bridge/status').json()['jobs'][0]
    assert stored['state'] == 'done' and stored['observed_status'] == 'finalized'
    with bridge.db() as conn:
        actual = json.loads(conn.execute('SELECT payload FROM migration_links WHERE project_id=?', ('local',)).fetchone()[0])
    assert actual['status'] == 'review'


def test_repaired_setup_waits_for_correct_extension_before_any_remote_work(monkeypatch, tmp_path):
    client, auth = setup_client()
    import video_tools as vt
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(vt, 'source', lambda session: ({'filename':'original.mp4','duration':10}, source))
    data = {'required_extension_version':'1.3.3', 'source_path':str(source.resolve()),
            'source_size':source.stat().st_size, 'source_mtime_ns':source.stat().st_mtime_ns,
            'project_updated_at':'now'}
    with bridge.db() as conn:
        conn.execute('DELETE FROM migration_links')
        conn.execute('UPDATE projects SET payload=?', (json.dumps({'session_id':'source','updated_at':'now'}),))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)', ('local','setup',json.dumps(data),0,'now'))
    for version in (None, '1.3.2', 'bad'):
        body = {'extension_version':version} if version else {}
        assert client.post('/api/bridge/worker/claim', headers=auth, json=body).json()['job'] is None
    job = client.post('/api/bridge/worker/claim', headers=auth, json={'extension_version':'1.3.3'}).json()['job']
    assert job['kind'] == 'migrate' and job['stage'] == 'setup'
    assert job['data']['source_path'] == str(source.resolve())


@pytest.mark.parametrize('stage', ['init_video','setup','create_project','confirm_tracking'])
def test_session_preflight_failure_resumes_without_marking_remote_write_uncertain(stage):
    client, auth = setup_client()
    now = time.time()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)', ('local',stage,'{}',0,'now'))
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) "
                     "VALUES ('auth','local',?,'running',?,?,'lease','migrate')", (REMOTE,now,now+600))
    result = client.post('/api/bridge/worker/jobs/auth/result',headers=auth,
                         json={'lease':'lease','error':bridge.SESSION_PREFLIGHT_ERROR})
    assert result.json()['stage'] == stage
    with bridge.db() as conn:
        assert conn.execute('SELECT next_run FROM migration_workflows').fetchone()[0] > now + 50
        assert json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])['status'] == 'review'


def test_auth_rejection_after_remote_request_remains_uncertain():
    client, auth = setup_client()
    now = time.time()
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)', ('local','create_project','{}',0,'now'))
        conn.execute("INSERT INTO bridge_jobs (id,project_id,remote_id,state,created,lease_until,lease,kind) "
                     "VALUES ('auth','local',?,'running',?,?,'lease','migrate')", (REMOTE,now,now+600))
    result = client.post('/api/bridge/worker/jobs/auth/result',headers=auth,
                         json={'lease':'lease','error':'GGAB 登录会话不可用；请在同一浏览器重新登录'})
    assert result.json()['stage'] == 'uncertain'


def test_fee_preview_uses_recorded_balance_only_for_same_verified_remote_project():
    client, _ = setup_client()
    with bridge.db() as conn:
        link = json.loads(conn.execute('SELECT payload FROM migration_links').fetchone()[0])
        link['balance_before'] = 530121
        conn.execute('UPDATE migration_links SET payload=?', (json.dumps(link),))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local','await_confirmation',json.dumps({'remote_project_id':REMOTE,'estimated_cost':4389}),0,'now'))
    flow = client.get('/api/bridge/status').json()['workflows'][0]
    assert flow['balance_before'] == 530121 and flow['stage'] == 'await_confirmation'
    with bridge.db() as conn:
        link['remote_project_id'] = 'other-project'
        conn.execute('UPDATE migration_links SET payload=?', (json.dumps(link),))
    assert client.get('/api/bridge/status').json()['workflows'][0]['balance_before'] is None


def video_workflow(tmp_path, monkeypatch, stage='upload_video'):
    import video_tools as vt
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original-video-bytes')
    record = {'filename':'original.mp4', 'duration':10, 'updated_at':'video-version'}
    monkeypatch.setattr(vt, 'source', lambda session: (record, source))
    snapshots = {}
    players = []
    folder = vt.MEDIA_ROOT / 'tracker'
    folder.mkdir(parents=True, exist_ok=True)
    with bridge.db() as conn:
        for slot, is_you in [('A1', True), ('B1', False)]:
            snap = {'id':'snapshot-' + slot, 'session_id':'session-one',
                    'video_version':'video-version', 'time':1, 'rotation':0,
                    'box':{'x':.1,'y':.1,'width':.2,'height':.3}}
            snapshots[slot] = snap
            players.append({'slot':slot,'name':slot,'handedness':'right',
                            'is_you':is_you,'snapshot_id':snap['id']})
            conn.execute('INSERT INTO snapshots VALUES (?,?)', (snap['id'], json.dumps(snap)))
            (folder / (snap['id'] + '.jpg')).write_bytes(b'jpeg')
        project = {'id':'local','title':'Before edit','match_date':'2026-09-28',
                   'session_id':'session-one','game_type':'singles','playing':True,
                   'players':players,'snapshots':snapshots,'segments':[],
                   'status':'ready','updated_at':'first-save'}
        data = {'source_path':str(source.resolve()), 'source_size':source.stat().st_size,
                'source_mtime_ns':source.stat().st_mtime_ns,
                'project_updated_at':'first-save','source':'uploaded',
                'video_id':'e7195a67-f326-46bc-8f39-08c3b2502575',
                'storage_path':'owner/original.mp4',
                'tus_url':'https://adscteqgwmvherkctzfm.supabase.co/storage/v1/upload/resumable/session',
                'upload_offset':4}
        conn.execute('INSERT INTO projects VALUES (?,?,?)', ('local',json.dumps(project),'first-save'))
        conn.execute('INSERT INTO migration_workflows VALUES (?,?,?,?,?)',
                     ('local',stage,json.dumps(data),0,'first-save'))
    client = TestClient(server.app)
    token = client.post('/api/bridge/pair',headers=LOCAL).json()['token']
    return client, {'Authorization':'Bearer ' + token}, source, project, data


def edit_workflow_project(project):
    changed = {**project, 'title':'After edit', 'updated_at':'second-save'}
    with bridge.db() as conn:
        conn.execute('UPDATE projects SET payload=?,updated_at=? WHERE id=?',
                     (json.dumps(changed),'second-save','local'))


def test_project_edit_during_video_upload_reuses_same_upload_and_latest_setup(tmp_path, monkeypatch):
    client, auth, _, project, data = video_workflow(tmp_path,monkeypatch)
    edit_workflow_project(project)
    job = client.post('/api/bridge/worker/claim',headers=auth,
                      json={'extension_version':'1.3.5'}).json()['job']
    assert job['stage'] == 'upload_video'
    assert job['project']['title'] == 'After edit'
    assert job['data']['video_id'] == data['video_id']
    assert job['data']['upload_offset'] == 4
    assert job['data']['project_updated_at'] == 'second-save'
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'upload_video'


def test_video_asset_rebases_project_saved_during_active_upload(tmp_path, monkeypatch):
    client, auth, source, project, _ = video_workflow(tmp_path,monkeypatch)
    edit_workflow_project(project)
    response = client.get('/api/bridge/worker/projects/local/video',headers=auth)
    assert response.status_code == 200 and response.content == source.read_bytes()
    with bridge.db() as conn:
        data = json.loads(conn.execute('SELECT payload FROM migration_workflows').fetchone()[0])
    assert data['project_updated_at'] == 'second-save'


@pytest.mark.parametrize('change', ['after_snapshots','source_changed'])
def test_rebase_still_blocks_unsafe_changes(tmp_path, monkeypatch, change):
    stage = 'upload_snapshots' if change == 'after_snapshots' else 'upload_video'
    client, auth, source, project, _ = video_workflow(tmp_path,monkeypatch,stage)
    edit_workflow_project(project)
    if change == 'source_changed':
        source.write_bytes(b'different-original-video-bytes')
    assert client.post('/api/bridge/worker/claim',headers=auth,
                       json={'extension_version':'1.3.5'}).json()['job'] is None
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'blocked'


def test_explicit_recovery_of_legacy_blocked_partial_upload(tmp_path, monkeypatch):
    client, auth, _, project, data = video_workflow(tmp_path,monkeypatch,'blocked')
    edit_workflow_project(project)
    with bridge.db() as conn:
        conn.execute('UPDATE migration_workflows SET payload=?',
                     (json.dumps({**data,'error':bridge.SOURCE_CHANGED_ERROR}),))
    recovered = bridge.resume_blocked_video_upload('local')
    assert recovered['stage'] == 'upload_video'
    assert recovered['remote_video_id'] == data['video_id']
    job = client.post('/api/bridge/worker/claim',headers=auth,
                      json={'extension_version':'1.3.5'}).json()['job']
    assert job['stage'] == 'upload_video' and job['data']['upload_offset'] == 4
    with pytest.raises(ValueError, match='并非可恢复'):
        bridge.resume_blocked_video_upload('local')
