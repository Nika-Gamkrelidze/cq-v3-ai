#!/usr/bin/env python3
"""Minimal GitHub push webhook -> safe redeploy. Stdlib only."""
import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()
PORT = int(os.environ.get("WEBHOOK_PORT", "9000"))
BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
REPO_DIR = os.environ.get("REPO_DIR", os.getcwd())
DEPLOY = os.path.join(REPO_DIR, "deploy", "deploy.sh")
LOG = os.environ.get("DEPLOY_LOG", os.path.join(REPO_DIR, "deploy", "webhook.log"))


def valid_signature(body: bytes, sig_header: str) -> bool:
    if not SECRET or not sig_header:
        return False
    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, msg: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if not valid_signature(body, self.headers.get("X-Hub-Signature-256", "")):
            self._send(401, "invalid signature")
            return
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            self._send(200, "pong")
            return
        if event != "push":
            self._send(202, "ignored: " + event)
            return
        try:
            ref = json.loads(body or b"{}").get("ref", "")
        except json.JSONDecodeError:
            self._send(400, "bad json")
            return
        if ref != "refs/heads/" + BRANCH:
            self._send(202, "ignored ref: " + ref)
            return
        # Respond immediately, then deploy in the background (GitHub times out at 10s).
        self._send(202, "deploying")
        with open(LOG, "a") as logf:
            subprocess.Popen(["bash", DEPLOY], stdout=logf, stderr=subprocess.STDOUT)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    if not SECRET:
        sys.stderr.write("WEBHOOK_SECRET not set; refusing to start\n")
        sys.exit(1)
    sys.stderr.write("webhook listening on :%d for pushes to %s\n" % (PORT, BRANCH))
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
