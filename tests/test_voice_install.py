"""Missing-weight setup follows real jobs and verified installation flags."""
import pytest

from scripts import install_voice_models


class Immediate:
    def is_set(self): return False
    def wait(self, seconds): return False


def test_empty_jobs_do_not_finish_until_catalogue_reports_installed(monkeypatch):
    counts, active, calls, messages = {}, [None], [], []
    repos = [model[0] for model in install_voice_models.MODELS]
    def api(session, url, path, data=None):
        calls.append((path, data))
        if path == "/models":
            return {"target": "local", "models": [{"repo_id": repo, "installed": counts.get(repo, 0) >= 3} for repo in repos]}
        if path == "/models/install":
            active[0] = data["repo_id"]
            return {"status": "started"}
        if path == "/models/install/status":
            counts[active[0]] = counts.get(active[0], 0) + 1
            return {"jobs": []}
    monkeypatch.setattr(install_voice_models, "api_json", api)
    install_voice_models.install_models(None, "http://127.0.0.1:3900", cancel=Immediate(), report=messages.append)
    assert counts == {repo: 3 for repo in repos}
    assert [data for path, data in calls if path == "/models/install"] == [{"repo_id": repo, "target": "local"} for repo in repos]
    assert len([message for message in messages if "Installed and verified" in message]) == 2


def test_existing_models_are_reused_without_download(monkeypatch):
    def api(session, url, path, data=None):
        assert path == "/models" and data is None
        return {"models": [{"repo_id": model[0], "installed": True} for model in install_voice_models.MODELS]}
    monkeypatch.setattr(install_voice_models, "api_json", api)
    install_voice_models.install_models(None, "http://127.0.0.1:3900", report=lambda _: None)


def test_failed_install_is_reported_and_stops_further_downloads(monkeypatch):
    posts = []
    def api(session, url, path, data=None):
        if path == "/models":
            return {"models": [{"repo_id": model[0], "installed": False} for model in install_voice_models.MODELS]}
        if path == "/models/install":
            posts.append(data)
            return {}
        return {"jobs": [{"repo_id": posts[0]["repo_id"], "state": "failed", "message": "Unavailable repository"}]}
    monkeypatch.setattr(install_voice_models, "api_json", api)
    with pytest.raises(RuntimeError, match="Unavailable repository"):
        install_voice_models.install_models(None, "http://127.0.0.1:3900", report=lambda _: None)
    assert len(posts) == 1


def test_model_install_never_redirects_to_remote_worker(monkeypatch):
    def api(session, url, path, data=None):
        assert path == "/models" and data is None
        return {"target": "remote-gpu", "models": []}
    monkeypatch.setattr(install_voice_models, "api_json", api)
    with pytest.raises(RuntimeError, match="remote worker"):
        install_voice_models.install_models(None, "http://127.0.0.1:3900")
