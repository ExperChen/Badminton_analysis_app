from test_accounts import environment, post, PASSWORD


def test_user_guide_requires_login_and_keeps_api_docs_admin_only(environment):
    admin, alice, bob, guest = environment["clients"]
    assert guest.get("/help", follow_redirects=False).status_code == 303
    for client in (admin, alice, bob):
        result = client.get("/help")
        assert result.status_code == 200
        assert "使用文档" in result.text and "不会自动生成分析" in result.text
        assert "恢复" not in result.text and "SHA-256" not in result.text
        assert result.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in result.headers["content-security-policy"]
    assert alice.get("/docs").status_code == 403
    assert admin.get("/docs").status_code == 200


def test_user_guide_links_preserve_the_account_context(environment):
    client = environment["clients"][3]
    prefix = "/s/" + "e" * 32
    signed_in = post(client, prefix + "/api/auth/login", {"email": "alice@example.test", "password": PASSWORD})
    assert signed_in.status_code == 200
    page = client.get(prefix + "/help")
    assert page.status_code == 200
    assert f'src="{prefix}/static/account-session.js"' in page.text
    assert f'href="{prefix}/account"' in page.text
    assert f'href="{prefix}/insights/match-breakdown"' in page.text
