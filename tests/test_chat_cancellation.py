"""Stopping inference closes the real HTTP connection, including model discovery."""
import json
import select
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from library.chat import ChatClient


@contextmanager
def model_server(mode):
    state = SimpleNamespace(mode=mode, started=threading.Event(), disconnected=threading.Event(),
                            requests=[], shutdown=threading.Event())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def wait_for_disconnect(self):
            state.started.set()
            while not state.shutdown.is_set():
                ready, _, _ = select.select([self.connection], [], [], .05)
                if ready and not self.connection.recv(1):
                    state.disconnected.set()
                    return

        def send_json(self, body, status=200):
            body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if state.mode == 'slow_discovery':
                self.wait_for_disconnect()
            else:
                self.send_json(dict(data=[dict(id='installed-model')]))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.requests.append(dict(body=body, auth=self.headers.get('Authorization')))
            if state.mode == 'slow_chat' and len(state.requests) == 1:
                self.wait_for_disconnect()
            elif state.mode == 'retry' and len(state.requests) == 1:
                self.send_json(dict(error='Unsupported response_format parameter'), 422)
            elif state.mode == 'unauthorized':
                self.send_json(dict(error='Authentication required'), 401)
            else:
                self.send_json(dict(choices=[dict(message=dict(content='Ready.'), finish_reason='stop')],
                                    usage=dict(prompt_tokens=12, completion_tokens=3)))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        yield state
    finally:
        state.shutdown.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('mode', ['slow_chat', 'slow_discovery'])
def test_cancel_closes_inference_socket_and_next_request_can_run(mode, monkeypatch):
    monkeypatch.setenv('NEXUS_MODEL_TOKEN', 'isolated-test-token')
    with model_server(mode) as server:
        cancel = threading.Event()
        settings = dict(server_url=server.url, api_model='installed-model')
        if mode == 'slow_discovery':
            settings.pop('api_model')
            settings.update(runtime_provider='lmstudio')
        client, errors = ChatClient(settings), []
        def infer():
            try:
                client.complete([dict(role='user', content='Slow request')], cancel=cancel)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=infer, daemon=True)
        thread.start()
        assert server.started.wait(3), 'Model request did not reach the isolated server'
        stopped_at = time.monotonic()
        cancel.set()
        thread.join(timeout=1)
        assert not thread.is_alive(), 'Stop must not wait for the model read timeout'
        assert time.monotonic() - stopped_at < 1
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
        assert str(errors[0]) == 'Request cancelled.'
        assert server.disconnected.wait(.5), 'Cancelled model generation kept its socket open'
        server.mode = 'ready'
        settings['api_model'] = 'installed-model'
        assert client.complete([], cancel=threading.Event())['content'] == 'Ready.'
        assert server.requests[-1]['auth'] == 'Bearer isolated-test-token'


def test_deadline_aborts_slow_http_instead_of_waiting_for_read_timeout(monkeypatch):
    monkeypatch.setenv('NEXUS_MODEL_TOKEN', 'isolated-test-token')
    with model_server('slow_chat') as server:
        client = ChatClient(dict(server_url=server.url, api_model='installed-model'))
        started_at = time.monotonic()
        with pytest.raises(TimeoutError, match='time budget'):
            client.complete([], cancel=threading.Event(), deadline=started_at + .8)
        assert server.started.is_set()
        assert time.monotonic() - started_at < 1.4
        assert server.disconnected.wait(.5)


def test_interruptible_transport_preserves_auth_payload_retries_and_usage(monkeypatch):
    monkeypatch.setenv('NEXUS_MODEL_TOKEN', 'isolated-test-token')
    with model_server('retry') as server:
        client = ChatClient(dict(server_url=server.url, api_model='installed-model',
                                 runtime_provider='lmstudio', enable_thinking=False,
                                 temperature=.35, top_k=20))
        result = client.complete([dict(role='user', content='¿Todo listo?')], structured=True,
                                 cancel=threading.Event(), deadline=time.monotonic() + 5)
        assert result == dict(content='Ready.', finish_reason='stop')
        assert len(server.requests) == 2
        first, second = [item['body'] for item in server.requests]
        assert first['response_format']['type'] == 'json_schema'
        assert second['response_format']['type'] == 'json_object'
        assert first['messages'] == second['messages'] == [dict(role='user', content='¿Todo listo?')]
        assert first['temperature'] == second['temperature'] == .35
        assert first['model'] == second['model'] == 'installed-model'
        assert all(item['auth'] == 'Bearer isolated-test-token' for item in server.requests)
        assert client.last_usage == dict(prompt_tokens=12, completion_tokens=3)
        assert client.last_timings['http_attempts'] == 2


def test_interruptible_transport_preserves_authentication_failure(monkeypatch):
    monkeypatch.setenv('NEXUS_MODEL_TOKEN', 'isolated-test-token')
    with model_server('unauthorized') as server:
        client = ChatClient(dict(server_url=server.url, api_model='installed-model'))
        with pytest.raises(RuntimeError, match='requires authentication') as error:
            client.complete([], cancel=threading.Event())
        assert 'isolated-test-token' not in str(error.value)
