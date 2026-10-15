"""Peter's inference budget starts at inference and abandons unfinished writes."""
import copy
import json
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from app.assistant.inference import DeadlineClient
from app.assistant.model_fallback import ModelToolError
from app.assistant.service import AssistantService
from app.calendar_storage import CalendarStore
from app.storage import Vault
from library.chat import ChatClient


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ReplyClient:
    def __init__(self, *replies):
        self.replies, self.calls = iter(replies), []
        self.settings = dict(context_size=16384, max_tokens=512)

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        reply = next(self.replies)
        if callable(reply):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(payload):
    return dict(content=json.dumps(payload))


def response(error=None):
    return SimpleNamespace(status_code=400 if error else 200, raise_for_status=lambda: None,
        json=lambda: dict(error=error) if error else {
            'choices': [dict(message=dict(content='{"answer":"Ready."}'))]})


def terminal(service):
    events = []
    while True:
        event = service.events.get(timeout=4)
        events.append(event)
        if event['type'] in {'answer', 'error'}:
            return event, events


def service_for(tmp_path, client):
    runtime = SimpleNamespace(client=client, settings=client.settings, ensure=lambda: None)
    return AssistantService(Vault(tmp_path / 'notes'), CalendarStore(tmp_path / 'calendar.json'),
                            runtime, tmp_path / 'conversation.json')


def test_budget_starts_at_first_inference_after_long_email_fetch():
    clock = Clock()
    client = ReplyClient('first result', 'next result')
    bounded = DeadlineClient(client, Event(), seconds=45, clock=clock)
    assert bounded.deadline is None
    clock.advance(3600)  # Header/attachment preparation is not model inference.
    assert bounded.complete([]) == 'first result'
    assert bounded.deadline == 3745
    clock.advance(44)
    assert bounded.complete([]) == 'next result'
    clock.advance(1)
    with pytest.raises(ModelToolError, match='No changes were saved'):
        bounded.complete([])
    assert len(client.calls) == 2


@pytest.mark.parametrize('error', [requests.Timeout('read timed out'), TimeoutError('expired')])
def test_transport_timeout_is_retryable_model_failure(error):
    bounded = DeadlineClient(ReplyClient(error), Event())
    with pytest.raises(ModelToolError, match='No changes were saved'):
        bounded.complete([])


def test_max_calls_stops_before_another_model_request():
    client = ReplyClient('one', 'two', 'must not run')
    bounded = DeadlineClient(client, Event(), max_calls=2)
    assert bounded.complete([]) == 'one'
    assert bounded.complete([]) == 'two'
    with pytest.raises(ModelToolError, match='No changes were saved'):
        bounded.complete([])
    assert bounded.calls == len(client.calls) == 2


def test_result_arriving_after_budget_is_discarded():
    clock = Clock()
    def late():
        clock.advance(46)
        return 'a late claim of success'
    bounded = DeadlineClient(ReplyClient(late), Event(), seconds=45, clock=clock)
    with pytest.raises(ModelToolError, match='No changes were saved'):
        bounded.complete([])


@pytest.mark.parametrize('phase', ['before', 'during', 'timeout'])
def test_cancellation_is_not_a_retryable_model_failure(phase):
    cancelled = Event()
    def action():
        cancelled.set()
        if phase == 'timeout':
            raise requests.Timeout('cancelled during read')
        return 'discard this answer'
    client = ReplyClient(action)
    bounded = DeadlineClient(client, cancelled)
    if phase == 'before':
        cancelled.set()
    with pytest.raises(ValueError, match='cancelled') as caught:
        bounded.complete([])
    assert not isinstance(caught.value, ModelToolError)
    assert len(client.calls) == (0 if phase == 'before' else 1)


def test_primary_staged_write_times_out_then_fallback_saves_only_its_action(tmp_path, monkeypatch):
    primary = ReplyClient(reply(dict(tool_calls=[dict(name='create_task',
        arguments=dict(title='Abandoned primary task', date='', time=''))])), requests.Timeout('slow'))
    alternative = ReplyClient(reply(dict(tool_calls=[dict(name='create_task',
        arguments=dict(title='Call the dentist', date='', time=''))])),
        reply(dict(answer='Done.', apply_changes=True)))
    service = service_for(tmp_path, primary)
    original_settings = copy.deepcopy(primary.settings)
    fallback_settings = copy.deepcopy(alternative.settings)
    clock, bounded_clients = Clock(), []
    def wrap(client, cancel):
        bounded = DeadlineClient(client, cancel, clock=clock)
        bounded_clients.append(bounded)
        return bounded
    monkeypatch.setattr('app.assistant.service.DeadlineClient', wrap)
    def fallback(cancel):
        assert service.calendar.entries() == [] and service.history.recent() == []
        assert bounded_clients[0].deadline == 145
        clock.advance(50)
        return SimpleNamespace(client=alternative, settings=alternative.settings)
    service.tool_fallback = SimpleNamespace(ensure=fallback, title='Another installed model', close=lambda: None)
    service.send('Create an unscheduled task called Call the dentist.')
    event, _ = terminal(service)
    assert event['type'] == 'answer' and event['operation_id']
    assert [task['title'] for task in service.calendar.entries()] == ['Call the dentist']
    assert len(service.history.recent()) == 1
    assert bounded_clients[1].deadline == 195 and bounded_clients[1].calls == 2
    assert primary.settings == original_settings and alternative.settings == fallback_settings
    assert all('deadline' not in settings for settings in (primary.settings, alternative.settings))


def test_both_models_timing_out_save_no_partial_tasks(tmp_path):
    staged = reply(dict(tool_calls=[dict(name='create_task',
        arguments=dict(title='Never committed', date='', time=''))]))
    service = service_for(tmp_path, ReplyClient(staged, TimeoutError('primary expired')))
    alternative = ReplyClient(staged, requests.Timeout('fallback expired'))
    service.tool_fallback = SimpleNamespace(ensure=lambda cancel: SimpleNamespace(
        client=alternative, settings=alternative.settings), title='Another installed model', close=lambda: None)
    service.send('Create an unscheduled task called Never committed.')
    event, _ = terminal(service)
    assert event['type'] == 'error' and 'Nothing was saved' in event['message']
    assert service.calendar.entries() == [] and service.history.recent() == []


def test_cancelled_timeout_does_not_switch_models_or_save(tmp_path):
    client = ReplyClient()
    service = service_for(tmp_path, client)
    def cancelled_timeout():
        service.cancel.set()
        raise requests.Timeout('read cancelled')
    client.replies = iter([cancelled_timeout])
    service.tool_fallback = SimpleNamespace(ensure=lambda cancel: pytest.fail('Cancellation must not switch models'))
    service.send('Create an unscheduled task called Stop here.')
    event, events = terminal(service)
    assert event['type'] == 'error' and 'cancelled' in event['message'].lower()
    assert service.calendar.entries() == [] and service.history.recent() == []
    assert not any('other local model' in event.get('text', '') for event in events)


@pytest.mark.parametrize('remaining, expected', [(45, (5, 30)), (7, (5, 7)), (.25, (.25, .25))])
def test_local_post_timeout_is_bounded_by_remaining_budget(monkeypatch, remaining, expected):
    clock, calls = Clock(), []
    monkeypatch.setattr('library.chat.time.monotonic', clock)
    def post(url, **kwargs):
        calls.append(kwargs)
        return response()
    monkeypatch.setattr(requests, 'post', post)
    settings = dict(server_url='http://127.0.0.1:8081/v1', api_model='installed-model', max_tokens=512)
    previous = copy.deepcopy(settings)
    ChatClient(settings).complete([], deadline=clock() + remaining, cancel=Event())
    assert calls[0]['timeout'] == expected and calls[0]['allow_redirects'] is False
    assert settings == previous


def test_unsupported_parameter_retries_use_remaining_budget_and_stop_at_deadline(monkeypatch):
    clock, calls = Clock(), []
    monkeypatch.setattr('library.chat.time.monotonic', clock)
    def post(url, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        clock.advance(5)
        return response('Unsupported response_format parameter')
    monkeypatch.setattr(requests, 'post', post)
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio',
                             api_model='installed-model'))
    with pytest.raises(TimeoutError, match='time budget'):
        client.complete([], structured=True, deadline=110)
    assert [call['timeout'] for call in calls] == [(5, 10), (5, 5)]
    assert calls[0]['json']['response_format']['type'] == 'json_schema'
    assert calls[1]['json']['response_format']['type'] == 'json_object'


@pytest.mark.parametrize('parameter', ['reasoning_effort', 'chat_template_kwargs'])
def test_other_unsupported_option_retries_do_not_reset_the_deadline(monkeypatch, parameter):
    clock, calls = Clock(), []
    monkeypatch.setattr('library.chat.time.monotonic', clock)
    def post(url, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            clock.advance(8)
            return response('Unsupported ' + parameter + ' parameter')
        return response()
    monkeypatch.setattr(requests, 'post', post)
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio',
                             api_model='installed-model', enable_thinking=False))
    client.complete([], deadline=110, cancel=Event())
    assert [call['timeout'] for call in calls] == [(5, 10), (2, 2)]
    assert parameter in calls[0]['json'] and parameter not in calls[1]['json']


def test_cancellation_during_unsupported_option_response_prevents_retry(monkeypatch):
    clock, cancelled, calls = Clock(), Event(), []
    monkeypatch.setattr('library.chat.time.monotonic', clock)
    def post(url, **kwargs):
        calls.append(kwargs)
        cancelled.set()
        return response('Unsupported response_format parameter')
    monkeypatch.setattr(requests, 'post', post)
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', api_model='installed-model'))
    with pytest.raises(ValueError, match='cancelled'):
        client.complete([], structured=True, deadline=110, cancel=cancelled)
    assert len(calls) == 1


def test_expired_budget_does_not_make_any_model_http_request(monkeypatch):
    monkeypatch.setattr('library.chat.time.monotonic', Clock())
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: pytest.fail('Expired inference must not discover models'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: pytest.fail('Expired inference must not post'))
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio'))
    with pytest.raises(TimeoutError, match='time budget'):
        client.complete([], deadline=100, cancel=Event())


def test_cancel_before_inference_does_not_discover_lmstudio_models(monkeypatch):
    cancelled = Event()
    cancelled.set()
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: pytest.fail('Cancelled inference must not discover models'))
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: pytest.fail('Cancelled inference must not post'))
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio'))
    with pytest.raises(ValueError, match='cancelled'):
        client.complete([], deadline=1000, cancel=cancelled)


def test_lmstudio_discovery_timeout_respects_remaining_budget(monkeypatch):
    clock, calls = Clock(), []
    monkeypatch.setattr('library.chat.time.monotonic', clock)
    def get(url, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout('discovery timed out')
    monkeypatch.setattr(requests, 'get', get)
    client = ChatClient(dict(server_url='http://127.0.0.1:8081/v1', runtime_provider='lmstudio'))
    with pytest.raises(requests.Timeout):
        client.complete([], deadline=100.25, cancel=Event())
    assert len(calls) == 1
    timeout = calls[0]['timeout']
    assert all(value <= .25 for value in timeout) if isinstance(timeout, tuple) else timeout <= .25
