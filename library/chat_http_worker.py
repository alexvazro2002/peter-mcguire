"""One private HTTP connection; terminated by chat_transport on interruption."""
import base64
import json
import sys

import requests


def main():
    try:
        request = json.load(sys.stdin)
        if isinstance(request['options'].get('timeout'), list):
            request['options']['timeout'] = tuple(request['options']['timeout'])
        response = requests.request(request['method'], request['url'], **request['options'])
        result = dict(status=response.status_code, headers=dict(response.headers),
                      reason=response.reason, encoding=response.encoding,
                      body=base64.b64encode(response.content).decode('ascii'))
        response.close()
    except requests.Timeout:
        result = dict(error='Timeout')
    except Exception:
        # Do not reflect request data, headers or credentials into diagnostics.
        result = dict(error='ConnectionError')
    json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()
