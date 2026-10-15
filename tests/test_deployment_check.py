import importlib
import json
import socket
import struct
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_deployment")


@pytest.fixture
def flaky_http():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def receive(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.path, self.headers.get("X-GitHub-Delivery"), body))
            if len(calls) == 1:
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                self.connection.close()
                self.close_connection = True
                return False
            return True

        def do_GET(self):
            if self.path == "/unauthorized":
                calls.append((self.path, None, b""))
                self.respond(401, {"detail": "Unauthorized"})
            elif self.receive():
                self.respond(200, {"status": "ok"})

        def do_POST(self):
            if self.receive():
                self.respond(202, {"status": "duplicate", "run_key": "fixture-run"})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls
        finally:
            server.shutdown()
            worker.join(timeout=5)


def test_readiness_recovers_from_connection_reset(checker, flaky_http):
    url, calls = flaky_http
    checker.wait_ready(url + "/healthz", timeout=5)
    assert len(calls) == 2


def test_ambiguous_webhook_retry_preserves_delivery_and_body(checker, flaky_http):
    url, calls = flaky_http
    status, response = checker.request_json(
        url + "/webhooks/github",
        {"X-GitHub-Delivery": "same-delivery"},
        b'{"fixture":true}',
        retry_for=5,
    )
    assert status == 202 and response["status"] == "duplicate"
    assert len(calls) == 2 and calls[0] == calls[1]


def test_authentication_failure_is_not_retried(checker, flaky_http):
    url, calls = flaky_http
    with pytest.raises(urllib.error.HTTPError) as error:
        checker.request_json(url + "/unauthorized", retry_for=5)
    assert error.value.code == 401
    assert len(calls) == 1
