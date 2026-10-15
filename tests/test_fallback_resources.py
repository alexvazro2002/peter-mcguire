"""Only Peter's temporary fallback load may be released after an inference retry."""
import copy
import json
from queue import Queue
from threading import Event
from types import SimpleNamespace

import pytest

from app.assistant.model_fallback import ModelToolError, ToolFallback
from app.assistant.service import AssistantService
from app.calendar_storage import CalendarStore
from app.storage import Vault
from library.lmstudio import LMStudio
from library.models import ModelChoice
from library.runtime import ModelRuntime


PRIMARY_KEY = 'provider/qwen3.5-9b-uncensored-hauhaucs-aggressive'
FALLBACK_KEY = 'provider/gemma-4-e4b'


def row(key, identifier, **extra):
    return dict(type='llm', modelKey=key, selectedVariant=key, identifier=identifier,
                contextLength=16384, trainedForToolUse=True, **extra)


def settings(**extra):
    return dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio',
                model_id='gemma', lmstudio_model_key=FALLBACK_KEY, **extra)


def release_fixture(monkeypatch, loaded, *, owned='peter-gemma'):
    config = settings()
    if owned is not None:
        config['_peter_loaded_alias'] = owned
    fallback = ToolFallback({})
    fallback.runtime = SimpleNamespace(settings=config)
    calls = []
    def run(self, args, deadline, cancel=None):
        calls.append(args)
        if args == ['ps', '--json']:
            return json.dumps(loaded)
        if args[:1] == ['unload']:
            loaded[:] = [item for item in loaded if item['identifier'] != args[1]]
            return ''
        pytest.fail('Unexpected LM Studio command: ' + str(args))
    monkeypatch.setattr(LMStudio, 'run', run)
    return fallback, calls


def test_release_unloads_only_owned_fallback_and_preserves_primary_and_user_models(monkeypatch):
    loaded = [row(PRIMARY_KEY, 'peter-qwen9b'), row(FALLBACK_KEY, 'peter-gemma'),
              row(FALLBACK_KEY, 'my-gemma-workspace')]
    fallback, calls = release_fixture(monkeypatch, loaded)
    fallback.release()
    assert calls == [['ps', '--json'], ['unload', 'peter-gemma']]
    assert [item['identifier'] for item in loaded] == ['peter-qwen9b', 'my-gemma-workspace']
    assert '_peter_loaded_alias' not in fallback.runtime.settings
    fallback.release()
    assert len(calls) == 2  # Ownership is consumed once; repeated cleanup is harmless.


@pytest.mark.parametrize('identifier', ['my-gemma-workspace', 'peter-gemma'])
def test_matching_user_loaded_model_is_never_unloaded_without_ownership(monkeypatch, identifier):
    loaded = [row(FALLBACK_KEY, identifier)]
    fallback, calls = release_fixture(monkeypatch, loaded, owned=None)
    fallback.release()
    assert calls == [] and loaded[0]['identifier'] == identifier


@pytest.mark.parametrize('owned', ['peter-qwen9b', 'my-gemma-workspace', '', 'peter-gemma-2'])
def test_ownership_marker_for_a_different_alias_cannot_unload_anything(monkeypatch, owned):
    loaded = [row(FALLBACK_KEY, 'peter-gemma')]
    fallback, calls = release_fixture(monkeypatch, loaded, owned=owned)
    fallback.release()
    assert calls == [] and len(loaded) == 1


@pytest.mark.parametrize('loaded', [
    [row(PRIMARY_KEY, 'peter-gemma')],
    [row(FALLBACK_KEY, 'user-loaded-gemma')],
    [row(FALLBACK_KEY, 'peter-gemma', deviceIdentifier='remote-computer')],
    [],
])
def test_release_checks_both_alias_and_local_model_identity(monkeypatch, loaded):
    original = copy.deepcopy(loaded)
    fallback, calls = release_fixture(monkeypatch, loaded)
    fallback.release()
    assert calls == [['ps', '--json']] and loaded == original
    assert '_peter_loaded_alias' not in fallback.runtime.settings


def test_failed_unload_retains_ownership_for_later_cleanup(monkeypatch, caplog):
    fallback = ToolFallback({})
    fallback.runtime = SimpleNamespace(settings=settings(_peter_loaded_alias='peter-gemma'))
    calls = []
    def run(self, args, deadline, cancel=None):
        calls.append(args)
        if args == ['ps', '--json']:
            return json.dumps([row(FALLBACK_KEY, 'peter-gemma')])
        if calls.count(['unload', 'peter-gemma']) == 1:
            raise RuntimeError('Temporary CLI failure')
        return ''
    monkeypatch.setattr(LMStudio, 'run', run)
    fallback.release()
    assert fallback.runtime.settings['_peter_loaded_alias'] == 'peter-gemma'
    assert 'could not release' in caplog.text
    fallback.release()
    assert '_peter_loaded_alias' not in fallback.runtime.settings
    assert calls.count(['unload', 'peter-gemma']) == 2


def test_shutdown_releases_owned_load_before_closing_the_runtime(monkeypatch):
    loaded = [row(PRIMARY_KEY, 'peter-qwen9b'), row(FALLBACK_KEY, 'peter-gemma')]
    fallback, calls = release_fixture(monkeypatch, loaded)
    closed = []
    def close():
        assert [item['identifier'] for item in loaded] == ['peter-qwen9b']
        assert '_peter_loaded_alias' not in fallback.runtime.settings
        closed.append(True)
    fallback.runtime.close = close
    fallback.close()
    assert closed == [True] and calls[-1] == ['unload', 'peter-gemma']


def lmstudio_fixture(tmp_path, monkeypatch, *, preloaded=False):
    primary, alternative = tmp_path / 'primary.gguf', tmp_path / 'gemma.gguf'
    primary.write_bytes(b'local GGUF placeholder')
    alternative.write_bytes(b'local GGUF placeholder')
    loaded = [row(PRIMARY_KEY, 'peter-qwen9b')]
    if preloaded:
        loaded.append(row(FALLBACK_KEY, 'my-gemma-workspace'))
    calls = []
    def run(self, args, deadline, cancel=None):
        calls.append(list(args))
        if args == ['server', 'status', '--json']:
            return json.dumps(dict(running=True, port=8081))
        if args == ['ls', '--json']:
            return json.dumps([row(FALLBACK_KEY, FALLBACK_KEY), row(PRIMARY_KEY, PRIMARY_KEY)])
        if args == ['ps', '--json']:
            return json.dumps(loaded)
        if args[:1] == ['load']:
            alias = args[args.index('--identifier') + 1]
            assert not any(item['identifier'] == alias for item in loaded)
            loaded.append(row(args[1], alias))
            return ''
        if args[:1] == ['unload']:
            loaded[:] = [item for item in loaded if item['identifier'] != args[1]]
            return ''
        pytest.fail('Unexpected LM Studio command: ' + str(args))
    monkeypatch.setattr(LMStudio, 'run', run)
    def get(*args, **kwargs):
        return SimpleNamespace(ok=True, raise_for_status=lambda: None,
            json=lambda: dict(data=[dict(id=item['identifier']) for item in loaded]))
    monkeypatch.setattr('requests.get', get)
    config = dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio', model_id='qwen9b',
                  model_path=str(primary), context_size=16384, _peter_loaded_alias='peter-qwen9b')
    fallback = ToolFallback(config, factory=ModelRuntime,
                            choices=lambda _: [ModelChoice('gemma', 'Installed fallback', str(alternative))])
    return fallback, loaded, calls, config


def test_released_runtime_can_reload_a_fresh_owned_fallback(tmp_path, monkeypatch):
    fallback, loaded, calls, primary_settings = lmstudio_fixture(tmp_path, monkeypatch)
    try:
        runtime = fallback.ensure(Event())
        assert runtime.settings['_peter_loaded_alias'] == 'peter-gemma'
        fallback.release()
        assert [item['identifier'] for item in loaded] == ['peter-qwen9b']
        assert '_peter_loaded_alias' not in runtime.settings and not runtime.closed
        assert fallback.ensure(Event()) is runtime
        assert runtime.settings['_peter_loaded_alias'] == 'peter-gemma'
        assert sum(command[0] == 'load' for command in calls) == 2
        assert sum(command[0] == 'unload' for command in calls) == 1
        assert primary_settings['_peter_loaded_alias'] == 'peter-qwen9b'
    finally:
        fallback.close()
    assert [item['identifier'] for item in loaded] == ['peter-qwen9b']


def test_reused_user_model_does_not_inherit_primary_model_ownership(tmp_path, monkeypatch):
    fallback, loaded, calls, primary_settings = lmstudio_fixture(tmp_path, monkeypatch, preloaded=True)
    try:
        runtime = fallback.ensure(Event())
        assert runtime.settings['api_model'] == 'my-gemma-workspace'
        assert '_peter_loaded_alias' not in runtime.settings
        fallback.release()
        assert not any(command[0] in {'load', 'unload'} for command in calls)
        assert primary_settings['_peter_loaded_alias'] == 'peter-qwen9b'
    finally:
        fallback.close()
    assert [item['identifier'] for item in loaded] == ['peter-qwen9b', 'my-gemma-workspace']


def test_new_load_rejects_a_user_model_already_using_the_fallback_alias(tmp_path, monkeypatch):
    fallback, loaded, calls, _ = lmstudio_fixture(tmp_path, monkeypatch)
    loaded.append(row(PRIMARY_KEY, 'peter-gemma'))
    try:
        with pytest.raises(ValueError, match='alias is already used'):
            fallback.ensure(Event())
        assert not any(command[0] in {'load', 'unload'} for command in calls)
        assert '_peter_loaded_alias' not in fallback.runtime.settings
    finally:
        fallback.close()
    assert [item['identifier'] for item in loaded] == ['peter-qwen9b', 'peter-gemma']


@pytest.mark.parametrize('terminal_type', ['answer', 'error'])
def test_service_releases_fallback_before_publishing_terminal_event(tmp_path, monkeypatch, terminal_type):
    runtime = SimpleNamespace(client=object(), settings={}, ensure=lambda: None)
    service = AssistantService(Vault(tmp_path / 'notes'), CalendarStore(tmp_path / 'calendar.json'),
                                runtime, tmp_path / 'conversation.json')
    release_entered, allow_release = Event(), Event()
    sequence = []
    class Engine:
        count = 0
        def __init__(self, *args, **kwargs):
            pass
        def run(self, *args):
            Engine.count += 1
            if Engine.count == 1:
                raise ModelToolError('Primary could not execute tools')
            if terminal_type == 'error':
                raise ModelToolError('Fallback could not execute tools')
            return dict(text='Saved.', sources=[], changes=[], operation_id=None)
    monkeypatch.setattr('app.assistant.service.Engine', Engine)
    def release():
        sequence.append('release entered')
        assert service.busy
        release_entered.set()
        assert allow_release.wait(3)
        sequence.append('release finished')
    service.tool_fallback = SimpleNamespace(
        ensure=lambda cancel: SimpleNamespace(client=object(), settings={}), title='Installed fallback', release=release)
    class RecordingQueue(Queue):
        def put(self, event, *args, **kwargs):
            if event['type'] in {'answer', 'error'}:
                sequence.append(event['type'])
            super().put(event, *args, **kwargs)
    service.events = RecordingQueue()
    try:
        service.send('Create an unscheduled task called Call the dentist.')
        assert release_entered.wait(3)
        assert not any(event['type'] in {'answer', 'error'} for event in list(service.events.queue))
        allow_release.set()
        while True:
            event = service.events.get(timeout=3)
            if event['type'] in {'answer', 'error'}:
                break
        assert event['type'] == terminal_type
        assert sequence == ['release entered', 'release finished', terminal_type]
    finally:
        allow_release.set()
