import json
import pytest
import cost_estimation
import bridge
from test_accounts import environment

EXPORT_PATHS = [
    '/api/my/export.csv', '/api/bridge/full-export.csv',
    '/api/matches/A/export?game=1', '/api/bridge/projects/project-A/shots.csv',
]

@pytest.mark.parametrize('path', EXPORT_PATHS)
def test_csv_downloads_are_disabled_for_all_roles_and_scoped_routes(environment, path):
    for client in environment['clients'][:3]:
        for method in ('get', 'head'):
            response = getattr(client, method)(path)
            assert response.status_code == 403
            assert 'text/csv' not in response.headers.get('content-type', '')
            assert 'content-disposition' not in response.headers
    # Scope path cannot bypass the centralized policy.
    guest = environment['clients'][3]
    from test_accounts import post, PASSWORD
    prefix = '/s/' + 'e' * 32
    assert post(guest, prefix + '/api/auth/login', {'email':'alice@example.test', 'password':PASSWORD}).status_code == 200
    assert guest.get(prefix + path).status_code == 403


def test_forecast_stays_private_and_does_not_trigger_recharge_without_official_quote(environment, monkeypatch):
    monkeypatch.setattr(cost_estimation, 'backfill', lambda conn: None)
    monkeypatch.setattr(cost_estimation, 'summary', lambda *args: {'id':'forecast', 'tokens':999999})
    with bridge.db() as conn:
        conn.execute('INSERT INTO migration_workflows VALUES(?,?,?,?,?)', (
            'project-A', 'review_project', json.dumps({'error':'SHA-256 mismatch D:/original.mp4', 'source':'debug'}), 0, 'now'))
    admin, alice = environment['clients'][:2]
    project = alice.get('/api/tracker/projects').json()[0]
    assert 'cost_prediction' not in project
    assert not project['quota']['needs_recharge']
    flow = alice.get('/api/bridge/status').json()['workflows'][0]
    assert flow['estimated_cost'] is None
    assert not flow['quota']['needs_recharge']
    assert not flow['confirmation_ready']
    assert 'cost_prediction' not in flow and 'source' not in flow and 'exported_csv' not in flow
    assert 'SHA-256' not in flow['error']
    diagnostic = admin.get('/api/bridge/status').json()['workflows'][0]
    assert diagnostic['cost_prediction']['tokens'] == 999999
    assert 'SHA-256' in diagnostic['error']
    assert alice.get('/api/tracker/projects/project-A/cost-history').status_code == 403
    assert alice.get('/api/tracker/projects/project-A/handoff.zip').status_code == 403
    assert alice.get('/api/admin/logs').status_code == 403
    assert admin.get('/api/admin/logs').status_code == 200


@pytest.mark.parametrize('path', ['/docs', '/redoc', '/openapi.json'])
def test_api_diagnostics_are_admin_only(environment, path):
    admin, alice, bob, guest = environment['clients']
    assert admin.get(path).status_code == 200
    assert alice.get(path).status_code == 403
    assert bob.get(path).status_code == 403
    assert guest.get(path, follow_redirects=False).status_code in (303,401)
