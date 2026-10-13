from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import docker_services as docker


def container(tmp_path):
    return {"Config": {"Image": "ghcr.io/alexta69/metube", "Env": []}, "State": {"Running": True},
            "Mounts": [{"Type": "bind", "Source": str(tmp_path), "Destination": "/downloads"}],
            "HostConfig": {"PortBindings": {"8081/tcp": [{"HostPort": "8082", "HostIp": "0.0.0.0"}]}}}


def test_native_start_and_restart_preserve_legacy_container_and_never_open_browser(tmp_path, monkeypatch):
    info = container(tmp_path)
    commands = []
    monkeypatch.setattr(docker, "docker_executable", lambda: "docker")
    monkeypatch.setattr(docker, "ensure_engine", lambda value: None)
    monkeypatch.setattr(docker, "inspect", lambda _docker, name, **kwargs: deepcopy(info) if name == "metube" else None)
    monkeypatch.setattr(docker, "command", lambda *args, **kwargs: commands.append(args) or SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(docker, "wait_http", lambda *args: None)
    monkeypatch.setattr(docker, "open_browser", lambda url: pytest.fail("Native starts must not open a browser"))
    metadata = docker.operate("metube", "start", root=tmp_path)
    assert metadata["container"] == "metube" and metadata["download_root"] == str(tmp_path)
    assert commands == []
    docker.operate("metube", "restart", root=tmp_path)
    assert commands == [("docker", "restart", "metube")]


def test_native_service_metadata_handles_prefix_audio_mount_and_excludes_secrets(tmp_path):
    info = container(tmp_path)
    info["Config"]["Env"] = ["URL_PREFIX=/tube", "AUDIO_DOWNLOAD_DIR=/downloads/audio", "SECRET=never-public"]
    info["Config"]["Labels"] = {"a/private": "secret"}
    result = docker.service_details("metube", "metube", info)
    assert result["url"] == "http://127.0.0.1:8082/tube/"
    assert result["audio_download_root"] == str(tmp_path / "audio")
    assert "never-public" not in repr(result)
    info["Mounts"][0]["Type"] = "volume"
    assert docker.service_details("metube", "metube", info)["download_root"] is None


def test_unregistered_service_and_action_are_rejected():
    with pytest.raises(ValueError):
        docker.operate("arbitrary-shell", "start")
    with pytest.raises(ValueError):
        docker.operate("metube", "rm")
