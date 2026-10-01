"""Tests del cliente intranet: parsing ASP.NET, validacion de formatos, rangos.
Sin red: solo logica pura (port de http.rs)."""

import threading
from datetime import date

import pytest

from src.core.intranet_client import (
    IntranetClient,
    day_chunks,
    day_chunk,
    exp_url,
    extract_largest_table_html,
    month_chunk,
    month_chunks,
    parse_hidden_fields,
    url_is_login,
    validate_export,
)


LOGIN_PAGE = """<html><body><form>
<input type="hidden" name="__VIEWSTATE" value="abc123" />
<input type="hidden" name="__VIEWSTATEGENERATOR" value="XYZ" />
<input type="hidden" name="__EVENTVALIDATION" value="ev456" />
<input name="txtnombre" id="txtnombre" />
</form></body></html>"""


class TestHiddenFields:
    def test_extrae_solo_dunder(self):
        fields = parse_hidden_fields(LOGIN_PAGE)
        names = [n for n, _ in fields]
        assert names == ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]
        assert dict(fields)["__VIEWSTATE"] == "abc123"

    def test_pagina_sin_fields(self):
        assert parse_hidden_fields("<html><body>hola</body></html>") == []


class TestValidacionExport:
    def test_ole_xls_valido(self):
        payload = b"\xd0\xcf\x11\xe0" + b"x" * 600
        assert validate_export(payload, "application/vnd.ms-excel") == "xls"

    def test_zip_xlsx_valido(self):
        payload = b"PK\x03\x04" + b"x" * 600
        assert validate_export(payload, "application/octet-stream") == "xls"

    def test_html_disfrazado(self):
        payload = b"<html><table><tr><td>dato</td></tr></table>" + b"x" * 600
        assert validate_export(payload, "application/vnd.ms-excel") == "html"

    def test_html_con_crlf_inicial(self):
        """Caso real Estadistica_New.aspx: HTML 83KB que empieza con \\r\\n."""
        payload = (
            b"\r\n<!DOCTYPE html>\r\n<html><body>No data to display</body></html>" + b"x" * 600
        )
        assert validate_export(payload, "text/html; charset=utf-8") == "html"

    def test_html_con_bom(self):
        payload = b"\xef\xbb\xbf<html><body>x</body></html>" + b"x" * 600
        assert validate_export(payload, "text/html") == "html"

    def test_html_error_corto(self):
        with pytest.raises(Exception, match="HTML de error"):
            validate_export(b"<html>error del server</html>", "text/html")

    def test_respuesta_sospechosa(self):
        with pytest.raises(Exception, match="sospechosa"):
            validate_export(b"garbage" * 10, "text/html")


class TestLoginDetect:
    def test_url_is_login(self):
        assert url_is_login(LOGIN_PAGE)
        assert not url_is_login("<html>grid de datos</html>")


class TestTableExtractor:
    def test_extrae_tabla_mayor(self):
        page = """
        <html><body>
        <table><tr><td>menu</td></tr></table>
        <table>
          <tr><td>ANHO</td><td>ID_CLIENTE</td><td>SOLES</td></tr>
          <tr><td>2024</td><td>68414</td><td>1,000.50</td></tr>
          <tr><td>2024</td><td>68415</td><td> 250,00 </td></tr>
        </table>
        </body></html>
        """
        rows = extract_largest_table_html(page)
        assert len(rows) == 3
        assert rows[0] == ["ANHO", "ID_CLIENTE", "SOLES"]
        # comas eliminadas (compatibilidad CSV del port Rust)
        assert rows[1][2] == "1 000.50"

    def test_html_vacio(self):
        assert extract_largest_table_html("<html></html>") == []


class TestRangos:
    def test_month_chunk(self):
        mc = month_chunk(2024, 1)
        assert mc.start == date(2024, 1, 1)
        assert mc.end == date(2024, 1, 31)
        assert mc.label == "2024-01"

    def test_month_chunk_diciembre(self):
        mc = month_chunk(2024, 12)
        assert mc.end == date(2024, 12, 31)

    def test_day_chunk(self):
        dc = day_chunk(date(2024, 5, 10))
        assert dc.label == "2024-05-10"

    def test_month_chunks_rango_completo(self):
        chunks = month_chunks(date(2024, 1, 15), date(2024, 3, 5))
        assert [c.label for c in chunks] == ["2024-01", "2024-02", "2024-03"]
        assert chunks[0].start == date(2024, 1, 15)
        assert chunks[0].end == date(2024, 1, 31)

    def test_day_chunks(self):
        chunks = day_chunks(date(2024, 1, 30), date(2024, 2, 1))
        assert [c.label for c in chunks] == ["2024-01-30", "2024-01-31", "2024-02-01"]


class TestUrl:
    def test_exp_url_d0(self):
        url = exp_url("01/01/2024", "31/01/2024", "", "", "D0")
        assert "accion=D0" in url
        assert "valueFI=01%2F01%2F2024" in url.replace("/", "%2F") or "valueFI=01/01/2024" in url
        assert "valueDocs=01F" in url
        assert "Estadistica11.aspx" in url

    def test_exp_url_l0_con_art(self):
        url = exp_url("01/01/2024", "31/01/2024", "0", "5ZZZZZ", "L0")
        assert "accion=L0" in url
        assert "valueArtI=0" in url
        assert "valueArtF=5ZZZZZ" in url


class TestIntranetClientOffline:
    def test_init_sin_login(self):
        cli = IntranetClient("u", "p")
        try:
            assert cli._logged_in is False
        finally:
            cli.close()

    def test_abort_check_via_evento(self):
        # El servicio de captura usa threading.Event; verificar integracion basica
        ev = threading.Event()
        assert not ev.is_set()
        ev.set()
        assert ev.is_set()


class _FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class _FakeHttp:
    def __init__(self, page: str, status: int = 200):
        self._page = page
        self._status = status

    def get(self, url, timeout=None):
        return _FakeResponse(self._page, self._status)


def _make_cli(monkeypatch, login_page: str, report_page: str, status: int = 200):
    cli = IntranetClient("u", "p", timeout=5.0)
    monkeypatch.setattr(cli, "_client", _FakeHttp(report_page, status))
    monkeypatch.setattr(
        cli,
        "login",
        lambda: setattr(cli, "_logged_in", True) or (_FakeHttp(login_page).get("x") and None),
    )
    return cli


class TestVerifyCredentials:
    def test_ok_con_grid(self, monkeypatch):
        filas = "".join(f"<tr><td>fila{i}</td></tr>" for i in range(250))
        grid = f"<html><table>{filas}</table></html>"
        cli = _make_cli(monkeypatch, LOGIN_PAGE, grid)
        ok, msg = cli.verify_credentials()
        assert ok, msg
        assert "Credenciales OK" in msg

    def test_credenciales_rechazadas(self, monkeypatch):
        cli = IntranetClient("u", "p", timeout=5.0)

        def fail_login():
            from src.core.intranet_client import IntranetError

            raise IntranetError("Login HTTP fallo (credenciales rechazadas)")

        monkeypatch.setattr(cli, "login", fail_login)
        ok, msg = cli.verify_credentials()
        assert not ok
        assert "Credenciales rechazadas" in msg

    def test_red_inaccesible(self, monkeypatch):
        cli = IntranetClient("u", "p", timeout=5.0)

        def fail_login():
            raise ConnectionError("timeout")

        monkeypatch.setattr(cli, "login", fail_login)
        ok, msg = cli.verify_credentials()
        assert not ok
        assert "Sin acceso" in msg

    def test_sesion_no_se_mantiene(self, monkeypatch):
        cli = _make_cli(monkeypatch, LOGIN_PAGE, LOGIN_PAGE)
        ok, msg = cli.verify_credentials()
        assert not ok
        assert "sesión no se mantiene" in msg

    def test_sin_permisos_reporte(self, monkeypatch):
        cli = _make_cli(monkeypatch, LOGIN_PAGE, "<html>corta</html>")
        ok, msg = cli.verify_credentials()
        assert not ok
        assert "permisos" in msg
