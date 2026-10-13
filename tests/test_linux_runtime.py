"""Linux migration checks use isolated data and never mutate LM Studio or the desktop."""
import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import DEFAULTS, load_config
from app.assistant.repository import Repository
from library.lmstudio import LMStudio, matches, parse_models
from library.models import model_matches
from library.chat import ChatClient
from app import docker_services
from tools.desktop_input import require_x11


def gemma(loaded=False):
    model = dict(type="llm", modelKey="google/gemma-4-e4b", selectedVariant="google/gemma-4-e4b@q4_k_m",
                 displayName="Gemma 4 E4B", deviceIdentifier=None)
    if loaded:
        model.update(identifier="google/gemma-4-e4b", contextLength=8192)
    return model


def test_lms_parser_handles_wakeup_and_rejects_invalid_metadata():
    assert parse_models("Waking up service...\n" + json.dumps([gemma()])) == [gemma()]
    for value in ('{"models":[]}', "service unavailable", "[1,2]"):
        with pytest.raises(ValueError):
            parse_models(value)


def test_local_gemma_selection_excludes_remote_and_embedding_models():
    assert matches(gemma(), {"model_id": "gemma"})
    assert matches(gemma(), {"lmstudio_model_key": "google/gemma-4-e4b@q4_k_m"})
    assert not matches(dict(gemma(), deviceIdentifier="remote-device"), {"model_id": "gemma"})
    assert not matches(dict(gemma(), type="embedding"), {"model_id": "gemma"})
    assert not matches(gemma(), {"lmstudio_model_key": "wrong-model"})


def fake_api(monkeypatch, identifier="google/gemma-4-e4b"):
    import requests
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": identifier}]})
    monkeypatch.setattr(requests, "get", lambda *a, **kw: response)


def test_reuse_loaded_gemma_never_loads_downloads_or_stops_it(monkeypatch):
    settings = deepcopy(DEFAULTS["library"])
    manager = LMStudio(settings)
    calls = []
    def run(args, *a):
        calls.append(args)
        if args == ["server", "status", "--json"]:
            return '{"running":true,"port":8081}'
        return json.dumps([gemma(True)])
    monkeypatch.setattr(manager, "run", run)
    fake_api(monkeypatch)
    manager.ensure(lambda: True)
    assert calls == [["server", "status", "--json"], ["ps", "--json"]]
    assert settings["api_model"] == "google/gemma-4-e4b" and settings["context_size"] == 8192
    assert settings["server_url"] == "http://127.0.0.1:8081/v1"
    assert model_matches(settings, [settings["api_model"]])


@pytest.mark.parametrize(('layers', 'offload'), [(0, 'off'), (-1, 'max')])
def test_startup_loads_only_existing_weights_and_loopback_api(monkeypatch, layers, offload):
    settings = deepcopy(DEFAULTS["library"])
    settings['gpu_layers'] = layers
    manager = LMStudio(settings)
    calls = []
    def run(args, *a):
        calls.append(args)
        if args == ["server", "status", "--json"]:
            return '{"running":false}'
        if args == ["ps", "--json"]:
            return json.dumps([gemma(True)] if any(c[0] == "load" for c in calls) else [])
        if args == ["ls", "--json"]:
            return json.dumps([gemma()])
        return "Started"
    monkeypatch.setattr(manager, "run", run)
    healthy = iter([False, True])
    fake_api(monkeypatch)
    manager.ensure(lambda: next(healthy))
    load = next(c for c in calls if c[0] == "load")
    assert load[1] == "google/gemma-4-e4b" and "--yes" in load
    assert settings["lmstudio_model_key"] == "google/gemma-4-e4b@q4_k_m"
    assert load[load.index("--context-length") + 1] == "8192"
    assert load[load.index('--gpu') + 1] == offload
    assert ["server", "start", "--port", "1234", "--bind", "127.0.0.1"] in calls
    assert not any(c[0] in {"get", "unload", "import"} for c in calls)


def test_missing_model_is_honest_and_never_downloaded(monkeypatch):
    manager = LMStudio(deepcopy(DEFAULTS["library"]))
    calls = []
    monkeypatch.setattr(manager, "run", lambda args, *a: calls.append(args) or ('{"running":false}' if args[0] == "server" else "[]"))
    with pytest.raises(ValueError, match="not installed"):
        manager.ensure(lambda: False)
    assert calls == [["server", "status", "--json"], ["ps", "--json"], ["ls", "--json"]]


def test_cancelled_cli_command_never_starts_a_process(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: pytest.fail("Cancelled before CLI startup"))
    with pytest.raises(ValueError, match="cancelled"):
        LMStudio({}).run(["ps", "--json"], 999999999, cancel)


def test_shared_chat_sends_verified_identifier(monkeypatch):
    import requests
    captured = []
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "Hello"}}]})
    monkeypatch.setattr(requests, "post", lambda *a, **kw: captured.append(kw["json"]) or response)
    ChatClient(dict(server_url="http://localhost:1234/v1", api_model="google/gemma-4-e4b")).complete([])
    assert captured[0]["model"] == "google/gemma-4-e4b"


def test_windows_paths_migrate_and_peter_settings_merge(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 2, "vault_path": "C:\\Users\\Alex\\notes",
                               "library": {"ai_python": "C:\\paddle-env\\Scripts\\python.exe", "context_size": 12000},
                               "peter": {"greeting": "Hello", "speech_enabled": False}}))
    config = load_config(path)
    assert config["library"]["ai_python"] == config["vault_path"] == ""
    assert config["library"]["context_size"] == 12000
    assert config["peter"]["greeting"] == "Hello" and config["peter"]["voice_url"].endswith(":3900")
    assert not config["peter"]["speech_enabled"]


def test_repository_tools_cannot_read_personal_runtime_data(tmp_path):
    (tmp_path / "personal-memory.json").write_text('{"private":true}')
    for directory in ("notes", "tasks", "services", "brick-backups"):
        folder = tmp_path / directory
        folder.mkdir()
        (folder / "private.md").write_text("private")
    (tmp_path / "ASSISTANT.md").write_text("documentation")
    repo = Repository(tmp_path)
    assert [r["path"] for r in repo.listing()["items"]] == ["ASSISTANT.md"]
    for path in ("personal-memory.json", "notes/private.md", "brick-backups/private.md"):
        with pytest.raises(ValueError):
            repo.read(path)


def test_podman_engine_is_rootless_without_daemon_start(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_services, "command", lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=0, stdout="linux\n"))
    docker_services.ensure_engine("/usr/bin/podman")
    assert calls == [("/usr/bin/podman", "info", "--format", "{{.Host.OS}}")]


def test_podman_mounts_are_local_and_selinux_labelled(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("metube", "stirling_pdf"):
        args = docker_services.new_container_arguments(docker_services.SERVICES[key], 9000, tmp_path, engine="podman")
        assert "127.0.0.1:9000:" in args[args.index("--publish") + 1]
        assert "--userns" in args and all(args[i + 1].endswith(":Z") for i, arg in enumerate(args) if arg == "--volume")
        assert "--privileged" not in args


def test_wayland_guard_runs_without_importing_input_libraries():
    with pytest.raises(RuntimeError, match="Wayland"):
        require_x11({"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"})
    require_x11({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"})


def test_model_token_env_precedence_and_file_errors_are_private(tmp_path, monkeypatch):
    from library.auth import request_options
    path = tmp_path / "local-token"
    path.write_text("file-test-token\n")
    monkeypatch.setenv("NEXUS_MODEL_TOKEN_FILE", str(path))
    monkeypatch.delenv("NEXUS_MODEL_TOKEN", raising=False)
    assert request_options()["headers"]["Authorization"] == "Bearer file-test-token"
    monkeypatch.setenv("NEXUS_MODEL_TOKEN", "environment-test-token")
    assert request_options()["headers"]["Authorization"] == "Bearer environment-test-token"
    monkeypatch.delenv("NEXUS_MODEL_TOKEN", raising=False)
    monkeypatch.setenv("NEXUS_MODEL_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="could not be read") as error:
        request_options()
    assert "file-test-token" not in str(error.value)


def test_authentication_failure_reports_action_without_echoing_credentials(monkeypatch):
    import requests
    from library.server import ModelServer
    monkeypatch.setenv("NEXUS_MODEL_TOKEN", "rejected-test-token")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: SimpleNamespace(status_code=401))
    with pytest.raises(RuntimeError, match="requires authentication") as error:
        ModelServer({"server_url": "http://127.0.0.1:8081/v1"}).healthy()
    assert "rejected-test-token" not in str(error.value)


def test_compact_tool_catalogue_retains_every_argument_and_tool_semantic(tmp_path):
    from app.assistant.engine import compact_catalogue
    from app.assistant.tools import Tools
    from app.assistant.transactions import History, Transaction
    from app.calendar_storage import CalendarStore
    from app.storage import Vault
    from app.memory import MemoryStore
    vault, calendar = Vault(tmp_path / "notes"), CalendarStore(tmp_path / "calendar.json")
    history = History(vault, calendar, vault.root / ".history/assistant", memory=MemoryStore(tmp_path / "memory.json"))
    tools = Tools(Transaction(vault, calendar, history))
    compact = compact_catalogue(tools.specs.values())
    omitted = {"maxLength", "maxItems", "minimum", "maximum", "additionalProperties"}
    def normalize(value):
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return normalize(compact["$defs"][value["$ref"].rsplit("/", 1)[1]])
        return {key: normalize(item) for key, item in value.items() if key not in omitted and not (key == "description" and not item)}
    assert normalize(compact["tools"]) == normalize(list(tools.specs.values()))
    assert len(json.dumps(compact)) < len(json.dumps(list(tools.specs.values()))) * .7


def test_full_assistant_tool_loop_fits_existing_8192_context_and_commits(tmp_path):
    from app.assistant.engine import Engine
    from app.assistant.transactions import History
    from app.calendar_storage import CalendarStore
    from app.storage import Vault
    from app.memory import MemoryStore
    vault, calendar = Vault(tmp_path / "notes"), CalendarStore(tmp_path / "calendar.json")
    memory = MemoryStore(tmp_path / "personal-memory.json")
    history = History(vault, calendar, vault.root / ".history/assistant", memory=memory)
    class Client:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            assert sum(len(item["content"]) for item in messages) < 8192 * 3 - 1000 * 3
            if self.calls == 1:
                reply = {"tool_calls": [{"name": "create_task", "arguments": {"title": "Check the workspace", "date": "", "time": ""}}]}
            else:
                reply = {"answer": "", "apply_changes": True}
            return {"content": json.dumps(reply)}
    client = Client()
    result = Engine(vault, calendar, history, client, {"context_size": 8192, "max_tokens": 1000}).run("Create an unscheduled task titled Check the workspace.")
    assert client.calls == 2 and result["operation_id"]
    assert calendar.entries()[0]["title"] == "Check the workspace"
    assert calendar.entries()[0]["date"] is calendar.entries()[0]["time"] is None
