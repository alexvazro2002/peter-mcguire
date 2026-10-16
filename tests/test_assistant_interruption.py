"""Stop isolates each turn and never commits the abandoned turn's staged writes."""
import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from app.assistant.cancellation import RequestHistory
from app.assistant.service import AssistantService
from app.assistant.transactions import Transaction
from app.calendar_storage import CalendarStore
from app.memory import LOCK
from app.storage import Vault
from library.chat import ChatClient
from library.runtime import ModelRuntime
from test_chat_cancellation import model_server


SETTINGS = dict(context_size=16384, max_tokens=512)
WRITE = 'Create an unscheduled task called Abandoned task.'


def reply(**value):
    return dict(content=json.dumps(value))


def staged_task():
    return reply(tool_calls=[dict(name='create_task', arguments=dict(
        title='Abandoned task', date='', time=''))])


class Model:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        result = next(self.results)
        return result() if callable(result) else result


def service_for(tmp_path, client=None, *, runtime=None):
    runtime = runtime or SimpleNamespace(client=client, settings=dict(SETTINGS), ensure=lambda: None)
    service = AssistantService(Vault(tmp_path / 'notes'), CalendarStore(tmp_path / 'calendar.json'),
                               runtime, tmp_path / 'conversation.json')
    service.tool_fallback = SimpleNamespace(
        ensure=lambda cancel: pytest.fail('An interrupted request must never switch models'),
        release=lambda: None, close=lambda: None)
    return service


def current_terminal(service, timeout=4):
    deadline, events = time.monotonic() + timeout, []
    while time.monotonic() < deadline:
        event = service.events.get(timeout=max(.01, deadline - time.monotonic()))
        events.append(event)
        if service.valid_event(event) and event['type'] in {'answer', 'error'}:
            return event, events
    pytest.fail('No current turn completed')


def drained(service):
    events = []
    while True:
        try:
            events.append(service.events.get_nowait())
        except queue.Empty:
            return events


def test_stop_real_http_after_staged_write_then_immediate_new_request(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_MODEL_TOKEN', 'isolated-interruption-token')
    with model_server('slow_chat') as server:
        class StagingHTTPClient(ChatClient):
            calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return staged_task()
                if self.calls == 2:
                    return super().complete(messages, **kwargs)
                return reply(answer='Four.', apply_changes=False)
        client = StagingHTTPClient(dict(SETTINGS, server_url=server.url, api_model='installed-model'))
        service = service_for(tmp_path, client)
        service.send(WRITE)
        old_token, old_id = service.cancel, service._request_id
        assert server.started.wait(3)
        assert service.calendar.entries() == [] and service.history.recent() == []
        stopped_at = time.monotonic()
        stopped = service.interrupt()
        assert time.monotonic() - stopped_at < .5
        assert stopped['interrupted'] and stopped['text'] == 'Stopped at your request.'
        assert not service.busy and old_token.is_set()
        service.send('What is two plus two?')
        new_token = service.cancel
        result, events = current_terminal(service)
        assert result['type'] == 'answer' and result['text'] == 'Four.'
        assert new_token is not old_token and not new_token.is_set() and old_token.is_set()
        assert server.disconnected.wait(.5)
        assert service.calendar.entries() == [] and service.vault.names() == []
        assert service.history.recent() == []
        assert not any(event['type'] in {'answer', 'error'} and event['request_id'] == old_id for event in events)
        assert [row['role'] for row in service.messages] == ['user', 'assistant', 'user']
        assert json.loads(service.path.read_text()) == service.messages


def test_late_old_result_cannot_clear_new_token_commit_or_replace_its_answer(tmp_path):
    blocked, unblock, cleaned = threading.Event(), threading.Event(), threading.Event()
    def late_result():
        blocked.set()
        assert unblock.wait(3)
        return reply(answer='Done.', apply_changes=True)
    old = Model(staged_task(), late_result)
    service = service_for(tmp_path, old)
    service.tool_fallback.release = cleaned.set
    service.send(WRITE)
    assert blocked.wait(2)
    old_token = service.cancel
    service.interrupt()
    replacement = Model(reply(answer='Four.', apply_changes=False))
    service.runtime.client = replacement
    service.send('What is two plus two?')
    new_token = service.cancel
    assert old_token.is_set() and not new_token.is_set() and old_token is not new_token
    assert replacement.calls == []  # Shared request state waits for old cleanup.
    unblock.set()
    result, events = current_terminal(service)
    assert cleaned.wait(1)
    assert result['type'] == 'answer' and result['text'] == 'Four.'
    assert not any(event['type'] == 'error' for event in events)
    assert service.calendar.entries() == [] and service.history.recent() == []
    assert len(replacement.calls) == 1 and old_token.is_set() and not new_token.is_set()


def test_stop_wins_before_final_commit_and_discards_the_entire_write(tmp_path, monkeypatch):
    reached, resume, cleaned = threading.Event(), threading.Event(), threading.Event()
    commit = Transaction.commit
    def pause_before_commit(self, request):
        reached.set()
        assert resume.wait(3)
        return commit(self, request)
    monkeypatch.setattr(Transaction, 'commit', pause_before_commit)
    service = service_for(tmp_path, Model(staged_task(), reply(answer='Done.', apply_changes=True)))
    service.tool_fallback.release = cleaned.set
    service.send(WRITE)
    assert reached.wait(2)
    assert service.interrupt()['text'] == 'Stopped at your request.'
    resume.set()
    assert cleaned.wait(2)
    assert service.calendar.entries() == [] and service.history.recent() == []
    assert all(not service.valid_event(event) for event in drained(service))
    assert service.interrupt() is None
    assert len(service.messages) == 2


def test_commit_wins_before_stop_reports_saved_changes_and_remains_undoable(tmp_path, monkeypatch):
    saved, resume, stopped = threading.Event(), threading.Event(), threading.Event()
    service = service_for(tmp_path, Model(staged_task(), reply(answer='Done.', apply_changes=True)))
    commit = service.history.commit
    def pause_after_save(*args, **kwargs):
        operation_id = commit(*args, **kwargs)
        saved.set()
        assert resume.wait(3)
        return operation_id
    monkeypatch.setattr(service.history, 'commit', pause_after_save)
    service.send(WRITE)
    assert saved.wait(2)
    messages = []
    def interrupt():
        messages.append(service.interrupt())
        stopped.set()
    thread = threading.Thread(target=interrupt, daemon=True)
    thread.start()
    assert not stopped.wait(.05)  # A verified multi-file commit owns the save boundary.
    resume.set()
    assert stopped.wait(2)
    assert len(messages) == 1 and messages[0]['operation_id']
    assert 'already been saved' in messages[0]['text'] and messages[0]['changes']
    assert len(service.calendar.entries()) == 1
    assert not service.busy
    service.undo(messages[0]['operation_id'])
    assert service.calendar.entries() == []
    assert all(not service.valid_event(event) for event in drained(service))


def test_transaction_guard_precedes_storage_lock_and_stop_undo_do_not_deadlock(tmp_path):
    service = service_for(tmp_path)
    service.append(dict(role='user', text='Create a note.'))
    service.busy = True
    history = service._request_history = RequestHistory(service.history, service.cancel, service.lock)
    tx = Transaction(service.vault, service.calendar, history)
    tx.write_note('Saved', '# Saved\n', create=True)
    tx.changed('Created Saved.')
    committed, interrupted, errors = [], [], []
    commit_started = threading.Event()
    def commit():
        commit_started.set()
        try:
            committed.append(tx.commit('Create a note.'))
        except Exception as error:
            errors.append(error)
    def stop_and_undo():
        try:
            message = service.interrupt()
            interrupted.append(message)
            service.undo(message['operation_id'])
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=commit, daemon=True)
    stopper = threading.Thread(target=stop_and_undo, daemon=True)
    # Hold storage while the worker enters its final save. The worker must own
    # the service guard before waiting for storage, matching Stop/Undo's order.
    LOCK.acquire()
    try:
        worker.start()
        assert commit_started.wait(1)
        deadline = time.monotonic() + 1
        guarded = False
        while time.monotonic() < deadline:
            if not service.lock.acquire(blocking=False):
                guarded = True
                break
            service.lock.release()
            time.sleep(.005)
        assert guarded, 'Commit took the storage lock before the request guard'
        stopper.start()
    finally:
        LOCK.release()
    worker.join(timeout=2)
    stopper.join(timeout=2)
    assert not worker.is_alive() and not stopper.is_alive(), 'Stop/Undo deadlocked with the old commit'
    assert errors == [] and len(committed) == len(interrupted) == 1
    assert interrupted[0]['operation_id'] == committed[0]
    assert service.vault.names() == [] and not service.busy


def test_fallback_cleanup_finishes_before_replacement_uses_shared_runtime(tmp_path):
    retry_started, resume_retry = threading.Event(), threading.Event()
    cleaning, finish_cleanup = threading.Event(), threading.Event()
    primary = Model(*[dict(content='invalid JSON')] * 3)
    service = service_for(tmp_path, primary)
    def late_retry():
        retry_started.set()
        assert resume_retry.wait(3)
        return reply(answer='Abandoned retry.', apply_changes=False)
    fallback = Model(late_retry)
    fallback_tokens, releases = [], []
    def ensure(cancel):
        fallback_tokens.append(cancel)
        return SimpleNamespace(client=fallback, settings=dict(SETTINGS))
    def release():
        releases.append(True)
        if len(releases) == 1:
            cleaning.set()
            assert finish_cleanup.wait(3)
    service.tool_fallback = SimpleNamespace(ensure=ensure, release=release, title='Test fallback')
    service.send('What is two plus two?')
    assert retry_started.wait(2)
    old_token = service.cancel
    service.interrupt()
    replacement = Model(reply(answer='Four.', apply_changes=False))
    service.runtime.client = replacement
    service.send('What is two plus two?')
    resume_retry.set()
    assert cleaning.wait(2) and replacement.calls == []
    finish_cleanup.set()
    result, events = current_terminal(service)
    assert result['type'] == 'answer' and result['text'] == 'Four.'
    assert fallback_tokens == [old_token] and old_token.is_set()
    assert len(replacement.calls) == 1
    assert not any(event['type'] == 'error' for event in events)
    assert service.history.recent() == []


def test_stop_cancels_cold_model_startup_and_next_turn_can_start(tmp_path):
    startup, stopped = threading.Event(), threading.Event()
    runtime = ModelRuntime(dict(SETTINGS, server_url='http://127.0.0.1:8081/v1'))
    calls, closed = [], []
    def ensure(cancel):
        calls.append(cancel)
        if len(calls) == 1:
            startup.set()
            assert cancel.wait(3)
            stopped.set()
            raise ValueError('Model startup cancelled.')
    runtime.server = SimpleNamespace(ensure=ensure, close=lambda: closed.append(True), process=None)
    runtime.client = Model(reply(answer='Four.', apply_changes=False))
    service = service_for(tmp_path, runtime=runtime)
    service.send('What is two plus two?')
    assert startup.wait(2)
    old_token = service.cancel
    service.interrupt()
    service.send('What is two plus two?')
    result, events = current_terminal(service)
    assert stopped.wait(.5) and len(calls) == 2
    assert result['type'] == 'answer' and result['text'] == 'Four.'
    assert old_token.is_set() and not service.cancel.is_set() and not runtime.cancel.is_set()
    assert closed == [True]
    assert not any(event['type'] == 'error' for event in events)


def test_stop_cancels_request_waiting_for_shared_startup_lock(tmp_path):
    runtime = ModelRuntime(dict(SETTINGS, server_url='http://127.0.0.1:8081/v1'))
    ensured, cleaned = [], threading.Event()
    runtime.server = SimpleNamespace(ensure=lambda cancel: ensured.append(cancel),
                                     close=lambda: None, process=None)
    runtime.client = Model(reply(answer='Four.', apply_changes=False))
    service = service_for(tmp_path, runtime=runtime)
    service.tool_fallback.release = cleaned.set
    runtime.lock.acquire()
    try:
        service.send('What is two plus two?')
        event = service.events.get(timeout=2)
        assert event['type'] == 'status' and event['message'] == 'Connecting to your local model'
        old_token = service.cancel
        service.interrupt()
        assert cleaned.wait(.7), 'Stopped request remained stuck behind shared model startup'
        assert ensured == [] and not runtime.cancel.is_set()
        service.send('What is two plus two?')
        assert service.cancel is not old_token and old_token.is_set()
    finally:
        runtime.lock.release()
    result, _ = current_terminal(service)
    assert result['type'] == 'answer' and result['text'] == 'Four.'
    assert len(ensured) == 1 and not ensured[0].is_set()
