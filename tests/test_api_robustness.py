"""Tests para src.core.api_robustness.check."""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone

import pytest


def test_parse_dt_formats():
    from src.core.api_robustness.check import _parse_iso

    assert _parse_iso("2026-10-02T17:03:41+00:00") is not None
    assert _parse_iso("2026-10-02 17:03:41") is not None
    assert _parse_iso("") is None
    assert _parse_iso(None) is None


def test_seconds_since():
    from src.core.api_robustness.check import _seconds_since

    now = datetime.now(timezone.utc)
    assert _seconds_since(now.strftime("%Y-%m-%dT%H:%M:%S+00:00")) < 1
    assert _seconds_since(None) == float("inf")
    assert _seconds_since("") == float("inf")


def test_is_server_machine_detects_api_repo():
    from src.core.api_robustness.check import _is_server_machine

    result = _is_server_machine()
    assert isinstance(result, bool)


def test_default_server_url():
    from src.core.api_robustness.check import _default_server_url

    url = _default_server_url()
    assert "://" in url or url == ""


def test_check_client_no_server():
    from src.core.api_robustness.check import check_client

    cs = check_client("http://127.0.0.1:59999")
    assert cs.http_8090_reachable is False
    assert len(cs.errors) > 0


def _api_disponible(url: str = "http://127.0.0.1:8090") -> bool:
    """True si el API responde health. Evita falsos rojos cuando el API esta caido."""
    import socket

    try:
        host = url.replace("http://", "").replace("https://", "").split("/")[0]
        if ":" in host:
            h, p = host.rsplit(":", 1)
            port = int(p)
        else:
            h, port = host, 80
        with socket.create_connection((h, port), timeout=3):
            return True
    except OSError:
        return False


def test_check_client_with_real_api():
    import pytest

    from src.core.api_robustness.check import check_client

    if not _api_disponible():
        pytest.skip("API no disponible en 127.0.0.1:8090")

    cs = check_client("http://127.0.0.1:8090")
    assert cs.http_8090_reachable is True
    assert cs.health_ok is True


def test_check_json_output():
    from src.core.api_robustness.check import check

    r = check(server_url="http://127.0.0.1:59999")
    assert r.severity in ("ok", "warning", "critical")
    assert r.client is not None
    assert r.client.http_8090_reachable is False


def test_check_severity_critical_when_offline():
    from src.core.api_robustness.check import check

    r = check(server_url="http://127.0.0.1:59999")
    assert r.severity == "critical"
    assert len(r.recommendations) > 0


def test_check_severity_ok_when_online():
    import pytest

    from src.core.api_robustness.check import check

    if not _api_disponible():
        pytest.skip("API no disponible en 127.0.0.1:8090")

    r = check(server_url="http://127.0.0.1:8090")
    assert r.severity in ("ok", "warning")
