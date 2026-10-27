import json

import requests

from auditor_bridge.config import BridgeError, auditor_url, secret


def request(method, path, *, body=None, idempotency_key=None, max_bytes=32 * 1024 * 1024):
    headers = {"Authorization": "Bearer " + secret("AUDITOR_BRIDGE_TOKEN")}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with requests.request(method, auditor_url() + path, json=body, headers=headers,
                              timeout=(5, 90), allow_redirects=False, stream=True) as response:
            if not 200 <= response.status_code < 300:
                msg = f"auditor_http_{response.status_code}"
                raise BridgeError(msg)
            data = bytearray()
            for block in response.iter_content(65536):
                data.extend(block)
                if len(data) > max_bytes:
                    msg = "auditor_response_too_large"
                    raise BridgeError(msg)
            return json.loads(data)
    except (requests.RequestException, ValueError) as exc:
        msg = "auditor_transport_or_json_error"
        raise BridgeError(msg) from exc
