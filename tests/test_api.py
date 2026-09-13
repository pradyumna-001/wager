"""API tests (minimal, no-DB subset; full API/graph tests are a follow-up)."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from pm_agent.api.app import create_app


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return (1,)


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _FakeCursor()


def test_healthz(monkeypatch):
    import pm_agent.api.routes as routes

    @contextmanager
    def fake_conn():
        yield _FakeConn()

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_slack_bad_signature_401():
    client = TestClient(create_app())
    body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"

    response = client.post(
        "/slack/interactions",
        content=body,
        headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=bad"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_signature"
