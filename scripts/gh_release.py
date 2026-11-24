#!/usr/bin/env python3
"""Create the GitHub release for a PySubZone tag.

Usage:
    python scripts/gh_release.py v0.5.0 [notes-file]

Reads GITHUB_TOKEN from /tmp/.tokens (or the GITHUB_TOKEN env var) and
POSTs the release to mackeos/PySubZone.  The tag must already exist on
the remote (push it first).  notes-file defaults to a generated body.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REPO = "mackeos/PySubZone"
TOKENS_FILE = "/tmp/.tokens"
API = "https://api.github.com"


def load_token() -> str:
    env = os.environ.get("GITHUB_TOKEN")
    if env:
        return env.strip()
    try:
        with open(TOKENS_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("GITHUB_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    raise SystemExit(f"no GITHUB_TOKEN in env or {TOKENS_FILE}")


def request(url: str, token: str, payload: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if payload else "GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.startswith("{") else body)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    tag = argv[1]
    token = load_token()

    status, info = request(f"{API}/repos/{REPO}/releases/tags/{tag}", token)
    if status == 200:
        print(f"release for {tag} already exists: {info['html_url']}")
        return 0

    notes_path = argv[2] if len(argv) > 2 else None
    if notes_path:
        body = open(notes_path, encoding="utf-8").read()
    else:
        body = f"PySubZone {tag}\n\nSee the repository README and commit history for details."

    payload = {
        "tag_name": tag,
        "name": f"PySubZone {tag}",
        "body": body,
        "target_commitish": "master",
        "draft": False,
        "prerelease": False,
        "generate_release_notes": False,
    }
    status, result = request(f"{API}/repos/{REPO}/releases", token, payload)
    if status in (200, 201):
        print(f"release created: {result['html_url']}")
        return 0
    print(f"failed ({status}): {result}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
