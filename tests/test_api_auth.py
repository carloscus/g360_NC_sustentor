"""Tests de src.core.api_auth (sin red: httpx.post mockeado)."""

import httpx

from src.core.api_auth import APIAuthClient, default_api_url


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_default_api_url_env(monkeypatch):
    monkeypatch.setenv("G360_API_URL", "http://192.168.1.10:8090/")
    assert default_api_url() == "http://192.168.1.10:8090"


def test_default_api_url_fallback(monkeypatch):
    monkeypatch.delenv("G360_API_URL", raising=False)
    assert default_api_url() == "http://127.0.0.1:8090"


def test_login_requiere_credenciales():
    cli = APIAuthClient("http://127.0.0.1:8090")
    r = cli.login("", "")
    assert not r.success
    assert not cli.is_authenticated


def test_login_ok_guarda_token(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/login")
        assert json == {"user": "ccusi", "password": "clave-test"}
        return _FakeResp(200, {"token": "tok.123.abc", "user": "ccusi"})

    monkeypatch.setattr(httpx, "post", fake_post)
    cli = APIAuthClient("http://127.0.0.1:8090")
    r = cli.login("ccusi", "clave-test")
    assert r.success and r.token == "tok.123.abc" and r.user == "ccusi"
    assert cli.is_authenticated and cli.token == "tok.123.abc"


def test_login_401_no_guarda_token(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _FakeResp(401, {"error": "credenciales invalidas"})

    monkeypatch.setattr(httpx, "post", fake_post)
    cli = APIAuthClient("http://127.0.0.1:8090")
    r = cli.login("ccusi", "mala")
    assert not r.success
    assert not cli.is_authenticated


def test_login_sin_red(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    cli = APIAuthClient("http://127.0.0.1:8090")
    r = cli.login("ccusi", "clave-test")
    assert not r.success
    assert "no disponible" in r.message.lower()
