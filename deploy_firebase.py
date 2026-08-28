#!/usr/bin/env python
"""Deploy static/ to Firebase Hosting using the REST API.

Uses the gcloud user credentials already on this machine, so it needs no
interactive `firebase login` and no CI token.

Usage:
    python deploy_firebase.py [--api-base https://your-backend.example]

--api-base rewrites static/config.js so the deployed page knows where the API
lives. Omit it to deploy config.js as-is.
"""
import argparse
import gzip
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SITE = "cookalong"
PROJECT = "cookalong-demo-2026"
STATIC = pathlib.Path(__file__).parent / "static"
BASE = "https://firebasehosting.googleapis.com/v1beta1"


def token():
    # On Windows gcloud is a .cmd shim, so resolve it rather than exec "gcloud".
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not exe:
        sys.exit("gcloud not found on PATH")
    return subprocess.run(
        [exe, "auth", "print-access-token"],
        capture_output=True, text=True, check=True, shell=False,
    ).stdout.strip()


def call(method, url, body=None, raw=None, content_type="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + TOKEN)
    request.add_header("x-goog-user-project", PROJECT)
    if data is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read()
            return json.loads(payload) if payload and content_type == "application/json" else {}
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {url}\n  HTTP {exc.code}: {exc.read().decode()[:600]}")


def set_api_base(api_base):
    config = STATIC / "config.js"
    text = config.read_text(encoding="utf-8")
    updated = re.sub(
        r'window\.COOKALONG_API_BASE = "[^"]*";',
        'window.COOKALONG_API_BASE = "%s";' % api_base,
        text,
    )
    if updated == text:
        sys.exit("Could not rewrite config.js - the COOKALONG_API_BASE line changed shape.")
    config.write_text(updated, encoding="utf-8", newline="")
    print(f"config.js -> API base {api_base or '(same origin)'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=None)
    args = parser.parse_args()
    if args.api_base is not None:
        set_api_base(args.api_base.rstrip("/"))

    # gzip deterministically: the manifest hash is of the compressed bytes
    files = {}
    for path in sorted(STATIC.rglob("*")):
        if not path.is_file():
            continue
        blob = gzip.compress(path.read_bytes(), mtime=0)
        digest = hashlib.sha256(blob).hexdigest()
        files["/" + path.relative_to(STATIC).as_posix()] = (digest, blob)
    if not files:
        sys.exit("static/ is empty")
    print("files:", ", ".join(sorted(files)))

    # The REST API does not read firebase.json - hosting config must be sent
    # with the version. Without this, Firebase serves HTML with max-age=3600
    # and every deploy stays invisible for an hour.
    config = {
        # glob matches the REQUEST path, so "**/*.html" never matches "/".
        # "**" is the only reliable way to keep the entry point uncached.
        "headers": [
            {"glob": "**", "headers": {"Cache-Control": "no-cache, max-age=0"}},
        ],
        "rewrites": [{"glob": "**", "path": "/index.html"}],
    }
    version = call("POST", f"{BASE}/sites/{SITE}/versions",
                   body={"config": config})["name"]
    print("version:", version)

    populated = call("POST", f"{BASE}/{version}:populateFiles",
                     body={"files": {p: h for p, (h, _) in files.items()}})
    required = set(populated.get("uploadRequiredHashes") or [])
    upload_url = populated.get("uploadUrl")
    print(f"{len(required)} file(s) to upload")

    by_hash = {h: b for (h, b) in files.values()}
    for digest in required:
        call("POST", f"{upload_url}/{digest}", raw=by_hash[digest],
             content_type="application/octet-stream")
        print("  uploaded", digest[:12])

    call("PATCH", f"{BASE}/{version}?update_mask=status", body={"status": "FINALIZED"})
    release = call("POST", f"{BASE}/sites/{SITE}/releases?versionName={version}", body={})
    print("released:", release.get("name", "?"))
    print(f"\nLive: https://{SITE}.web.app")


TOKEN = token()

if __name__ == "__main__":
    main()
