import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

import accounts
import app as server
from test_accounts import environment, post, PASSWORD


def test_register_without_admin_approval_has_zero_quota_and_isolation(environment):
    admin, _, _, guest = environment["clients"]
    before = admin.get('/api/me').json()
    assert guest.get('/api/auth/status').json()['registration_open'] is True
    response = post(guest, '/api/auth/register', {'email': ' New@Example.test ', 'password': PASSWORD})
    assert response.status_code == 200, response.text
    user = response.json()
    assert user['role'] == 'user' and user['email'] == 'new@example.test'
    assert guest.get('/api/me').json()['id'] == user['id']
    assert guest.get('/api/matches').json() == []
    assert guest.get('/api/tracker/player-options').json() == []
    assert guest.get('/api/admin/users').status_code == 403
    quota = guest.get('/api/tokens/me').json()
    assert quota['available'] == 0 and quota['transactions'] == [] and 'ggab' not in quota
    assert user['id'] in {u['id'] for u in admin.get('/api/admin/users').json()}
    assert admin.get('/api/me').json() == before
    with accounts.database() as c:
        row = c.execute('SELECT password_hash,role FROM users WHERE id=?', (user['id'],)).fetchone()
        assert row['password_hash'].startswith('$argon2id$') and PASSWORD not in row['password_hash']
        assert c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 1
        audit = c.execute("SELECT reason FROM audit WHERE action='self_register' AND target_user=?", (user['id'],)).fetchone()
        assert before['id'] in audit['reason']


def test_no_role_injection_duplicate_or_admin_session_switch(environment, monkeypatch):
    admin, _, _, guest = environment['clients']
    before = admin.get('/api/me').json()
    for extra in ({'role': 'admin'}, {'managed_by': 'other'}, {'enabled': 1}):
        assert post(guest, '/api/auth/register', {'email': 'hacker@example.test', 'password': PASSWORD, **extra}).status_code == 422
    assert post(admin, '/api/auth/register', {'email': 'other@example.test', 'password': PASSWORD}).status_code == 409
    assert admin.get('/api/me').json() == before
    monkeypatch.setattr(accounts, 'hash_password', lambda _: pytest.fail('duplicate must be rejected before hashing'))
    assert post(guest, '/api/auth/register', {'email': 'ADMIN@example.test', 'password': PASSWORD}).status_code == 409
    assert post(guest, '/api/auth/register', {'email': 'admin@example.test', 'password': 'short'}).status_code == 422


def test_register_never_bootstraps_an_administrator(monkeypatch):
    monkeypatch.setattr(accounts, 'ENFORCE_AUTH', True)
    monkeypatch.setattr(accounts, 'hash_password', lambda _: pytest.fail('missing admin must reject before hashing'))
    client = TestClient(server.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234))
    assert client.get('/api/auth/status').json() == {'initialized': False, 'registration_open': False}
    assert post(client, '/api/auth/register', {'email': 'first@example.test', 'password': PASSWORD}).status_code == 403
    assert not accounts.initialized()


def test_private_https_signup_uses_secure_scoped_cookie_and_requires_vpn(environment, monkeypatch):
    monkeypatch.setenv('GGAB_PUBLIC_ORIGIN', 'https://video.example.test')
    monkeypatch.setenv('GGAB_REQUIRE_VPN_IDENTITY', '1')
    client = TestClient(server.app, base_url='https://video.example.test')
    body = {'email': 'vpn@example.test', 'password': PASSWORD}
    assert post(client, '/api/auth/register', body).status_code == 403
    client.headers['Tailscale-User-Login'] = 'member@example.test'
    prefix = '/s/' + 'e' * 32
    result = post(client, prefix + '/api/auth/register', body)
    assert result.status_code == 200, result.text
    assert 'Secure' in result.headers['set-cookie'] and 'HttpOnly' in result.headers['set-cookie']
    assert 'Path=' + prefix + '/' in result.headers['set-cookie']
    assert client.get(prefix + '/api/me').json()['role'] == 'user'
    assert client.get(prefix + '/docs').status_code == 403


def test_disabled_or_ambiguous_admin_and_opt_out_close_registration(environment, monkeypatch):
    guest = environment['clients'][3]
    monkeypatch.setenv('GGAB_SELF_REGISTRATION', '0')
    assert guest.get('/api/auth/status').json()['registration_open'] is False
    assert post(guest, '/api/auth/register', {'email': 'new@example.test', 'password': PASSWORD}).status_code == 403
    monkeypatch.setenv('GGAB_SELF_REGISTRATION', '1')
    with accounts.database() as c:
        c.execute("UPDATE users SET enabled=0 WHERE role='admin'")
    assert guest.get('/api/auth/status').json()['registration_open'] is False
    assert post(guest, '/api/auth/register', {'email': 'new@example.test', 'password': PASSWORD}).status_code == 403


def test_daily_limit_persists_across_clients_and_email_changes(environment, monkeypatch):
    monkeypatch.setenv('GGAB_PUBLIC_ORIGIN', 'https://video.example.test')
    monkeypatch.setenv('GGAB_REQUIRE_VPN_IDENTITY', '1')
    monkeypatch.setattr(accounts, 'hash_password', lambda _: 'test-only-hash')
    for i in range(6):
        client = TestClient(server.app, base_url='https://video.example.test', client=(f'192.0.2.{i}', 1234))
        client.headers['Tailscale-User-Login'] = 'same-member@example.test'
        result = post(client, '/api/auth/register', {'email': f'new{i}@example.test', 'password': PASSWORD})
        assert result.status_code == (200 if i < 5 else 429), result.text
    with accounts.database() as c:
        assert c.execute("SELECT COUNT(*) FROM audit WHERE action='self_register'").fetchone()[0] == 5
        key = hashlib.sha256(b'self-register-day:same-member@example.test').hexdigest()
        assert c.execute('SELECT attempts FROM auth_limits WHERE key=?', (key,)).fetchone()[0] == 5
