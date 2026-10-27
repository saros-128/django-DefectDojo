import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class BridgeError(Exception):

    """Safe, bounded error code suitable for API responses and logs."""


def origin(value):
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (ValueError, AttributeError) as exc:
        msg = "invalid_origin"
        raise BridgeError(msg) from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        msg = "invalid_origin"
        raise BridgeError(msg)
    host = parsed.hostname.lower().rstrip(".")
    if not host:
        msg = "invalid_origin"
        raise BridgeError(msg)
    if ":" in host:
        host = f"[{host}]"
    if port and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def auditor_url():
    return origin(os.environ["AUDITOR_BRIDGE_URL"])


def secret(name):
    value = Path(os.environ[name + "_FILE"]).read_text(encoding="utf-8").strip()
    if len(value) < 24:
        msg = "invalid_secret_configuration"
        raise BridgeError(msg)
    return value
