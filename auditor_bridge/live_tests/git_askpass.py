#!/usr/bin/env python3
"""Supply fixture credentials only to the isolated GitLab service."""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main():
    if len(sys.argv) != 2:
        return 1
    prompt = sys.argv[1]
    match = re.search(r"https?://[^'\s]+", prompt)
    if not match:
        return 1
    try:
        target = urlsplit(match.group())
        if target.scheme != "http" or target.hostname != "gitlab" or target.port not in {None, 80}:
            return 1
    except ValueError:
        return 1
    if prompt.startswith("Username for "):
        print("oauth2")  # noqa: T201 -- Git askpass requires the response on stdout.
    elif prompt.startswith("Password for "):
        print(Path("/run/live-secrets/gitlab-token").read_text(encoding="utf-8").strip())  # noqa: T201 -- Git askpass protocol.
    else:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
