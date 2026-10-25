import pytest
import video_tools


@pytest.fixture(autouse=True)
def isolated_media(tmp_path, monkeypatch):
    monkeypatch.setenv("YUXU_FEEDBACK_WORKER_DISABLED", "1")
    monkeypatch.setenv("YUXU_FEEDBACK_NO_MAIL", "1")
    monkeypatch.setenv("YUXU_UPLOAD_STORAGE", "local")
    # Tests never import or export into the user's real video library.
    monkeypatch.setattr(video_tools, "MEDIA_ROOT", tmp_path / "media")
    monkeypatch.setattr(video_tools, "ACTIVE", {})
    # Legacy feature tests remain anonymous; account integration tests enable auth explicitly.
    import accounts
    monkeypatch.setattr(accounts, "ENFORCE_AUTH", False)
