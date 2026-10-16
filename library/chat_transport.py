"""Interruptible local-model HTTP without leaving inference sockets running."""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path


def _stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=.25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def request(method, url, *, cancel=None, deadline=None, **options):
    """Terminate the HTTP connection promptly when its request is cancelled.

    The worker owns the socket, so stopping it also disconnects the request at
    the model server. A background thread alone would leave that socket open.
    Credentials and conversation content travel only through private pipes.
    """
    import requests

    if cancel is None:
        return getattr(requests, method.lower())(url, **options)

    def check():
        if cancel.is_set():
            raise ValueError('Request cancelled.')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('The local model request exceeded its time budget.')

    check()
    payload = json.dumps(dict(method=method, url=url, options=options)).encode('utf-8')
    process = subprocess.Popen(
        [sys.executable, '-I', str(Path(__file__).with_name('chat_http_worker.py'))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        while True:
            check()
            try:
                output, _ = process.communicate(input=payload, timeout=.025)
                break
            except subprocess.TimeoutExpired:
                # communicate retains pending writes and buffered output across
                # timeouts; pass the input only on its first invocation.
                payload = None
        check()
        if process.returncode:
            raise requests.ConnectionError('The local model connection was interrupted.')
        try:
            result = json.loads(output)
            if result.get('error'):
                if result['error'] == 'Timeout':
                    raise requests.Timeout('The local model request timed out.')
                raise requests.ConnectionError('The local model connection failed.')
            response = requests.Response()
            response.status_code = result['status']
            response.headers.update(result['headers'])
            response.url = url
            response.reason = result['reason']
            response.encoding = result['encoding']
            response._content = base64.b64decode(result['body'], validate=True)
            response._content_consumed = True
            return response
        except (KeyError, TypeError, ValueError) as error:
            raise requests.ConnectionError('The local model transport returned an invalid response.') from error
    finally:
        _stop(process)
