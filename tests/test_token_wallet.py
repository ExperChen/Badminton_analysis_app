import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

import accounts
import bridge
import token_wallet as tokens
from test_accounts import environment, post, SUBJECT

REMOTE = 'aaaaaaaa-cccc-4ddd-8eee-ffffffffffff'


def grant(env, amount=1000, request_id='grant-request-0001'):
    admin = env['clients'][0]
    return post(admin, f'/api/admin/users/{env["identities"][1]["id"]}/tokens',
                {'amount': amount, 'request_id': request_id})


def quote(env, pid='project-A', remote=REMOTE, cost=400):
    with bridge.db() as conn:
        conn.execute('INSERT OR REPLACE INTO migration_links VALUES(?,?,?)', (pid, json.dumps({
            'remote_project_id': remote, 'status': 'review', 'estimated_cost': cost,
            'balance_before': 516924, 'association': {'verified': True}}), 'now'))
        conn.execute('INSERT OR REPLACE INTO migration_workflows VALUES(?,?,?,?,?)',
                     (pid, 'await_confirmation', json.dumps({'remote_project_id': remote, 'estimated_cost': cost}), 0, 'now'))


def worker(env):
    admin, _, _, guest = env['clients']
    token = post(admin, '/api/bridge/pair', {}).json()['token']
    headers = {'Authorization': 'Bearer ' + token, 'X-GGAB-Subject': SUBJECT}
    assert guest.post('/api/bridge/worker/source', json={'user_id': SUBJECT}, headers=headers).status_code == 200
    return guest, headers


def observe(env, status='finalized', billing=None, job='observe-1'):
    guest, headers = worker(env)
    at = time.time()
    with bridge.db() as conn:
        conn.execute("INSERT INTO bridge_jobs(id,project_id,remote_id,state,created,lease_until,lease,kind) VALUES(?,'project-A',?,'running',?,?,'lease','sync_status')", (job, REMOTE, at, at + 60))
    body = {'lease': 'lease', 'status': status}
    if billing is not None:
        body['billing'] = billing
    return guest.post('/api/bridge/worker/jobs/' + job + '/result', json=body, headers=headers)


def receipt(actual=250, remote=REMOTE, key='receipt-1'):
    return {'remote_project_id': remote, 'actual_tokens': actual, 'receipt_id': key, 'source': 'ggab_token_ledger'}


def confirm(env, cost=400, pid='project-A', remote=REMOTE):
    return post(env['clients'][1], '/api/bridge/confirm/' + pid, {'remote_project_id': remote, 'estimated_cost': cost})


def test_allocation_is_admin_only_idempotent_and_account_isolated(environment):
    admin, alice, bob, guest = environment['clients']
    url = f'/api/admin/users/{environment["identities"][1]["id"]}/tokens'
    body = {'amount': 1000, 'request_id': 'grant-request-0001'}
    for client in (alice, bob):
        assert post(client, url, body).status_code == 403
        assert client.get('/api/admin/tokens').status_code == 403
    assert guest.get('/api/tokens/me').status_code == 401
    assert grant(environment).status_code == 200
    assert grant(environment).json()['balance'] == 1000
    assert grant(environment, 900).status_code == 409
    for invalid in [-1, 0, True, 1.5]:
        assert post(admin, url, {**body, 'amount': invalid}).status_code == 422
    assert alice.get('/api/tokens/me').json()['available'] == 1000
    assert bob.get('/api/tokens/me').json()['available'] == 0
    assert len(alice.get('/api/tokens/me').json()['transactions']) == 1
    assert 'ggab' not in alice.get('/api/tokens/me').json()


def test_insufficient_quota_blocks_confirm_and_foreign_project_before_remote_submission(environment):
    quote(environment)
    result = confirm(environment)
    assert result.status_code == 402 and '需要充值' in result.text
    alice = environment['clients'][1]
    flow = alice.get('/api/bridge/status').json()['workflows'][0]
    assert not flow['confirmation_ready'] and flow['quota']['needs_recharge']
    assert flow['balance_before'] is None
    assert 'balance_before' not in alice.get('/api/tracker/projects/project-A/migration').json()
    assert post(environment['clients'][2], '/api/bridge/confirm/project-A', {'remote_project_id': REMOTE, 'estimated_cost': 400}).status_code == 404
    with bridge.db() as conn:
        assert conn.execute('SELECT stage FROM migration_workflows').fetchone()[0] == 'await_confirmation'
        assert not conn.execute('SELECT * FROM token_holds').fetchall()


def test_hold_does_not_charge_until_real_result_and_settles_only_once(environment):
    quote(environment)
    grant(environment, 1000)
    assert confirm(environment).status_code == 200
    alice = environment['clients'][1]
    data = alice.get('/api/tokens/me').json()
    assert (data['balance'], data['reserved'], data['available']) == (1000, 400, 600)
    assert observe(environment, 'processing', receipt(), job='early').status_code == 422
    assert observe(environment, billing=receipt(remote='wrong'), job='wrong').status_code == 409
    assert observe(environment, billing=None, job='missing').json()['ok']
    assert alice.get('/api/tokens/me').json()['balance'] == 1000
    assert alice.delete('/api/tracker/projects/project-A', headers={'X-GGAB-Local': '1'}).status_code == 409
    assert observe(environment, billing=receipt(), job='complete').json()['ok']
    data = alice.get('/api/tokens/me').json()
    assert (data['balance'], data['reserved'], data['available']) == (750, 0, 750)
    assert observe(environment, billing=receipt(), job='duplicate').json()['ok']
    assert alice.get('/api/tokens/me').json()['balance'] == 750
    assert observe(environment, billing=receipt(249), job='changed').status_code == 409
    assert len([r for r in data['transactions'] if r['kind'] == 'settlement']) == 1
    summary = environment['clients'][0].get('/api/admin/tokens').json()
    assert summary['summary']['consumed'] == 250
    assert summary['summary']['settled_projects'] == 1
    assert summary['summary']['reserved'] == 0
    assert next(u for u in summary['users'] if u['id'] == environment['identities'][1]['id'])['consumed'] == 250


def test_actual_above_quote_records_debt_and_history_is_not_charged(environment):
    quote(environment)
    grant(environment, 400)
    assert confirm(environment).status_code == 200
    assert observe(environment, billing=receipt(500)).json()['ok']
    assert environment['clients'][1].get('/api/tokens/me').json()['available'] == -100
    with bridge.db() as conn:
        assert not tokens.settle(conn, 'old-match', 'old-remote', 500, 'old-receipt', {})


def test_parallel_projects_cannot_overreserve_same_account(environment):
    grant(environment, 700)
    identity = environment['identities'][1]
    accounts.claim('project', 'parallel', identity['id'])
    def reserve(pid):
        context = accounts.principal.set(identity)
        try:
            with bridge.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                tokens.reserve(conn, pid, pid + '-remote', 500)
            return 200
        except HTTPException as exc:
            return exc.status_code
        finally:
            accounts.principal.reset(context)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(reserve, ['project-A', 'parallel'])) == [200, 402]
    assert environment['clients'][1].get('/api/tokens/me').json()['available'] == 200


def test_verified_activity_settles_after_completion_and_never_by_balance_difference(environment):
    quote(environment)
    grant(environment)
    assert confirm(environment).status_code == 200
    guest, headers = worker(environment)
    activity = {'id': 'activity-1', 'amount': -250, 'label': 'Track Match Automatically',
                'pending': False, 'projectId': REMOTE}
    def sync(items):
        return guest.post('/api/bridge/worker/token-balance', headers=headers, json={'total': 516924, 'activity': items})
    assert sync([activity]).status_code == 200
    assert environment['clients'][1].get('/api/tokens/me').json()['balance'] == 1000
    assert observe(environment).json()['ok']
    assert environment['clients'][1].get('/api/tokens/me').json()['balance'] == 750
    guest, headers = worker(environment)
    assert sync([activity]).status_code == 200
    assert environment['clients'][1].get('/api/tokens/me').json()['balance'] == 750
    admin_data = environment['clients'][0].get('/api/tokens/me').json()
    assert admin_data['ggab']['total'] == 516924 and not admin_data['ggab']['stale']
    assert 'ggab' not in environment['clients'][2].get('/api/tokens/me').json()
    with bridge.db() as conn:
        assert tokens.activity_receipt(conn, 'unmatched') is None


def test_ambiguous_or_pending_activity_is_not_a_charge(environment):
    with bridge.db() as conn:
        tokens.remember_activity(conn, [{'id': 'pending', 'amount': -250, 'label': 'Track Match Automatically', 'pending': True, 'projectId': REMOTE}])
        assert tokens.activity_receipt(conn, REMOTE) is None
        tokens.remember_activity(conn, [{'id': str(i), 'amount': -250, 'label': 'Track Match Automatically', 'pending': False, 'projectId': REMOTE} for i in range(2)])
        assert tokens.activity_receipt(conn, REMOTE) is None


def test_official_hold_transaction_identifies_activity_without_names_or_project_fields(environment):
    quote(environment)
    grant(environment)
    assert confirm(environment).status_code == 200
    guest, headers = worker(environment)
    activity = {'id': 'official-transaction', 'amount': -1929, 'label': 'Track Match Automatically', 'pending': False}
    assert guest.post('/api/bridge/worker/token-balance', headers=headers, json={'total': 516924, 'activity': [activity]}).status_code == 200
    with bridge.db() as conn:
        tokens.bind_receipt(conn, 'project-A', REMOTE, 'official-transaction')
    assert observe(environment).json()['ok']
    assert environment['clients'][1].get('/api/tokens/me').json()['balance'] == -929


def test_unsubmitted_quote_releases_quota_but_submitted_and_mismatched_receipts_do_not(environment):
    quote(environment)
    grant(environment)
    assert confirm(environment).status_code == 200
    with bridge.db() as conn:
        tokens.release_unsubmitted(conn, 'project-A')
    assert environment['clients'][1].get('/api/tokens/me').json()['available'] == 1000
    with bridge.db() as conn:
        with pytest.raises(HTTPException):
            tokens.settle(conn, 'project-A', REMOTE, 250, 'receipt', {})
    quote(environment)
    assert confirm(environment).status_code == 200
    with bridge.db() as conn:
        tokens.submitted(conn, 'project-A')
        tokens.release_unsubmitted(conn, 'project-A')
        tokens.bind_receipt(conn, 'project-A', REMOTE, 'exact-receipt')
    with bridge.db() as conn:
        with pytest.raises(HTTPException):
            tokens.settle(conn, 'project-A', REMOTE, 250, 'wrong-receipt', {})
    balance = environment['clients'][1].get('/api/tokens/me').json()
    assert (balance['balance'], balance['reserved'], balance['available']) == (1000, 400, 600)


def test_balance_and_activity_failures_preserve_last_good_balance_and_actual_consumption(environment):
    quote(environment)
    grant(environment)
    assert confirm(environment).status_code == 200
    assert observe(environment, billing=receipt()).json()['ok']
    guest, headers = worker(environment)
    url = '/api/bridge/worker/token-balance'
    assert guest.post(url, headers=headers, json={'total': 500000, 'activity_error': 'GGAB 明细暂时不可用'}).status_code == 200
    current = environment['clients'][0].get('/api/admin/tokens').json()
    assert current['ggab']['total'] == 500000 and not current['ggab']['stale']
    assert current['ggab']['activity_error']
    assert current['summary']['consumed'] == 250
    assert guest.post(url, headers=headers, json={'error': '登录失效'}).status_code == 200
    current = environment['clients'][0].get('/api/admin/tokens').json()
    assert current['ggab']['total'] == 500000 and current['ggab']['stale']
    assert current['summary']['consumed'] == 250
    assert guest.post(url, headers=headers, json={'total': 499000}).status_code == 200
    current = environment['clients'][0].get('/api/admin/tokens').json()
    assert current['ggab']['activity_error'] is None and current['ggab']['activity_observed_at']
    assert current['summary']['consumed'] == 250  # Shared balance changes never infer a child's charge.
