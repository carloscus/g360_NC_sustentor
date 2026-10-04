"""Tests de ventas_api_client y sync_api (sin red: httpx mockeado)."""

import httpx
import pytest

from src.core.sync_api import SyncAPI
from src.core.ventas_api_client import VentaAPIClient, VentaAPIError


class _FakeResp:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)[:200]
        self.content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _cli(monkeypatch, routes):
    cli = VentaAPIClient("http://127.0.0.1:8091", "tok")

    def fake_request(method, url, headers=None, params=None):
        assert headers.get("Authorization") == "Bearer tok"
        for prefix, resp in routes:
            if url.endswith(prefix):
                return resp
        raise AssertionError(f"ruta inesperada: {url}")

    def fake_get(url, headers=None, params=None):
        return fake_request("GET", url, headers, params)

    def fake_post(url, headers=None, json=None):
        return fake_request("POST", url, headers, json)

    monkeypatch.setattr(cli.client, "request", fake_request)
    monkeypatch.setattr(cli.client, "get", fake_get)
    monkeypatch.setattr(cli.client, "post", fake_post)
    return cli


def test_frescura_usa_day_checksums_no_checksums(monkeypatch):
    """No debe pegarle a /api/checksums: ese endpoint mata el timeout del server."""
    cli = _cli(
        monkeypatch,
        [
            ("/api/status", _FakeResp(200, {"filas": 10, "meses": 2, "fecha_hasta": "2026-10-02"})),
            (
                "/api/day-checksums",
                _FakeResp(200, {"dias": [{"dia": "2026-10-01"}, {"dia": "2026-10-02"}]}),
            ),
        ],
    )
    sync = SyncAPI(cli)
    fr = sync.frescura("2026-10-01", "2026-10-02")
    assert fr["filas"] == 10 and fr["n_dias"] == 2
    assert fr["capturado_en_ultimo"] is None


def test_frescura_sobrevive_a_day_checksums_caido(monkeypatch):
    """Si day-checksums falla, la frescura sigue devolviendo status."""
    cli = _cli(
        monkeypatch,
        [
            ("/api/status", _FakeResp(200, {"filas": 7})),
            ("/api/day-checksums", _FakeResp(500, {"error": "timeout"})),
        ],
    )
    fr = SyncAPI(cli).frescura("2026-10-01", "2026-10-02")
    assert fr["filas"] == 7 and fr["n_dias"] == 0


def test_folios_y_by_folios(monkeypatch):
    cli = _cli(
        monkeypatch,
        [
            ("/api/folios", _FakeResp(200, {"folios": ["F1", "F2", "F3"]})),
            (
                "/api/ventas/by-folios",
                _FakeResp(200, {"filas": [{"folio": "F1"}, {"folio": "F3"}]}),
            ),
        ],
    )
    sync = SyncAPI(cli)
    falt = sync.folios_faltantes("2026-09-01", "2026-09-30", ["F2"])
    assert falt == ["F1", "F3"]
    filas = sync.fetch_faltantes(falt)
    assert len(filas) == 2


def test_diff_folios_puro():
    assert SyncAPI.diff_folios(["a", "b", "a"], ["b"]) == ["a"]
    assert SyncAPI.diff_folios([], ["a"]) == []


def test_error_http_lanza(monkeypatch):
    cli = _cli(monkeypatch, [("/api/status", _FakeResp(401, {"error": "token requerido"}))])
    with pytest.raises(VentaAPIError):
        cli.status()


def test_data_pasa_filtros(monkeypatch):
    visto = {}

    cli = VentaAPIClient("http://127.0.0.1:8091", "tok")

    def fake_get(url, headers=None, params=None):
        visto.update(params or {})
        return _FakeResp(200, {"datos": [], "filas": 0})

    monkeypatch.setattr(cli.client, "get", fake_get)
    cli.data("ventas", limit=5, filtros={"id_cliente": "eq.00068414"})
    assert visto["limit"] == 5 and visto["id_cliente"] == "eq.00068414"


def test_login_guarda_token(monkeypatch):
    cli = VentaAPIClient("http://127.0.0.1:8091")

    def fake_post(url, headers=None, json=None):
        assert "Authorization" not in headers
        return _FakeResp(200, {"token": "ccusi.123.abc", "user": "ccusi"})

    monkeypatch.setattr(cli.client, "post", fake_post)
    data = cli.login("ccusi", "clave-test")
    assert cli.api_token == "ccusi.123.abc" and cli.user == "ccusi"
    assert data["user"] == "ccusi"


def test_red_caida_lanza(monkeypatch):
    cli = VentaAPIClient("http://127.0.0.1:8091", "tok")

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cli.client, "get", boom)
    with pytest.raises(VentaAPIError):
        cli.status()


# ── SyncAPI.aplicar (escritura local) ──────────────────────────────────


def _fila(folio, dia="2026-10-01", **kw):
    """Fila con las 51 columnas de INSERT_COLS, listas para insertar."""
    from src.core.ventas_db import INSERT_COLS

    base = {c.strip(): "" for c in INSERT_COLS.split(",")}
    base.update(
        {
            "tipo_operacion": "Venta",
            "moneda": "PEN",
            "anho": dia[:4],
            "mes": dia[5:7],
            "fecha_orig": dia,
            "mes_ref": dia[:7],
            "folio_unico": folio,
            "tpo_doc": "BDI",
            "serie_doc": "BDI20625",
            "nro_doc": folio[-4:],
            "cantidad": 1.0,
            "soles": 10.0,
            "precio_unitario": 10.0,
            "id_cliente": "00068414",
            "doc_cliente": "00068414",
            "nom_cliente": "PRIMAVERA",
        }
    )
    base.update(kw)
    return base


class _FakeApi:
    """Cliente API minimo para ejercitar el diff + escritura.

    `dias` simula lo que devuelve /api/day-checksums; si no se pasa, se deriva
    de las filas que devolveria el servidor.
    """

    def __init__(self, folios=(), filas=(), dias=None):
        self._folios = list(folios)
        self._filas = list(filas)
        self._dias = dias
        self.pedidos = []
        self.pedidos_dias = []

    def folios(self, desde, hasta):
        return list(self._folios)

    def day_checksums(self, desde, hasta):
        if self._dias is not None:
            return {"dias": [dict(d) for d in self._dias]}
        agg: dict = {}
        for f in self._filas:
            dia = str(f.get("fecha_orig", ""))[:10]
            if not dia:
                continue
            n, tot = agg.get(dia, (0, 0.0))
            agg[dia] = (n + 1, round(tot + float(f.get("soles") or 0), 2))
        return {"dias": [{"dia": d, "filas": n, "soles": s} for d, (n, s) in agg.items()]}

    def ventas_by_folios(self, folios, desde="", hasta="", chunk=400):
        self.pedidos.append(list(folios))
        ped = set(folios)
        # Las filas sin folio_unico se pasan igual: es la anomalia que
        # aplicar() tiene que descartar, no el cliente.
        return [
            f
            for f in self._filas
            if not str(f.get("folio_unico") or "").strip() or f["folio_unico"] in ped
        ]

    def ventas_por_dia(self, dia, limit=1000, offset=0):
        self.pedidos_dias.append(dia)
        return [f for f in self._filas if str(f.get("fecha_orig", ""))[:10] == dia][
            offset : offset + limit
        ]


def _filas_locales(conn, mes_ref="2026-10"):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT folio_unico, COUNT(*), ROUND(SUM(soles),2) FROM ventas "
            "WHERE mes_ref = ? GROUP BY folio_unico ORDER BY folio_unico",
            (mes_ref,),
        )
    ]


def test_aplicar_sin_cambios_no_escribe(tmp_db):
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1")])
    res = SyncAPI(_FakeApi(["F1"])).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["estado"] == "sin_cambios"
    assert res["filas"] == 0 and res["folios_faltantes"] == 0
    assert res["modo"] == "dias"
    assert _filas_locales(conn) == [("F1", 1, 10.0)]


def test_aplicar_no_refetcha_dias_que_cuadran(tmp_db):
    """Dia con (filas, soles) igual al remoto: ni una llamada de venta."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1"), _fila("F2")])
    api = _FakeApi(["F1", "F2"], [_fila("F1"), _fila("F2")])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["estado"] == "sin_cambios" and res["dias_desfasados"] == []
    assert api.pedidos_dias == [] and api.pedidos == []


def test_aplicar_inserta_solo_lo_faltante(tmp_db):
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1")])
    api = _FakeApi(["F1", "F2"], [_fila("F2", soles=20.0), _fila("F2", soles=30.0)])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["estado"] == "ok" and res["filas"] == 2
    assert res["modo"] == "dias" and res["dias_desfasados"] == ["2026-10-01"]
    # Se re-fetcha el dia completo, no la lista de folios faltantes.
    assert api.pedidos_dias == ["2026-10-01"] and api.pedidos == []
    # F1 intacto, F2 con sus 2 lineas
    assert _filas_locales(conn) == [("F1", 1, 10.0), ("F2", 2, 50.0)]


def test_aplicar_detecta_folio_recapturado_por_dia(tmp_db):
    """El diff por folio no ve un folio ya local que la API recapturo.

    El folio F1 existe en ambos lados, asi que `folios_faltantes` es 0; el
    cambio se ve solo por el agregado del dia (2 filas / 50.0 vs 1 / 10.0).
    """
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", soles=10.0)])
    api = _FakeApi(["F1"], [_fila("F1", soles=20.0), _fila("F1", soles=30.0)])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["folios_faltantes"] == 0
    assert res["estado"] == "ok" and res["filas"] == 2
    assert _filas_locales(conn) == [("F1", 2, 50.0)]


def test_aplicar_refetcha_solo_el_dia_desfasado(tmp_db):
    """Un dia al dia no se toca aunque el rango entero tenga cambios."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02")])
    api = _FakeApi(
        ["F1", "F2"],
        [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02"), _fila("F3", "2026-10-02")],
    )
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-02", conn=conn)
    assert res["dias_desfasados"] == ["2026-10-02"]
    assert api.pedidos_dias == ["2026-10-02"]
    assert sorted(f for f, _, _ in _filas_locales(conn)) == ["F1", "F2", "F3"]


def test_aplicar_fallback_a_folios_si_day_checksums_falla(tmp_db):
    """Si /api/day-checksums cae, se usa el diff de folios (no se rompe)."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1")])
    api = _FakeApi(["F1", "F2"], [_fila("F2")])

    def boom(*a, **k):
        raise VentaAPIError("se cayo la API")

    api.day_checksums = boom
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["modo"] == "folios" and res["estado"] == "ok"
    assert api.pedidos == [["F2"]]
    assert _filas_locales(conn) == [("F1", 1, 10.0), ("F2", 1, 10.0)]


def test_aplicar_reemplaza_folio_recapturado_sin_duplicar(tmp_db):
    """El mismo folio con mas lineas: replaces, no append."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", soles=10.0)])
    api = _FakeApi(["F1"], [_fila("F1", soles=20.0), _fila("F1", soles=30.0)])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn, solo_faltantes=False)
    assert res["filas"] == 2 and res["modo"] == "rango"
    assert _filas_locales(conn) == [("F1", 2, 50.0)]


def test_aplicar_es_idempotente(tmp_db):
    conn = tmp_db.get_conn()
    api = _FakeApi(["F1", "F2"], [_fila("F1"), _fila("F2")])
    sync = SyncAPI(api)
    primera = sync.aplicar("2026-10-01", "2026-10-31", conn=conn)
    segunda = sync.aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert primera["filas"] == 2
    assert segunda["estado"] == "sin_cambios" and segunda["filas"] == 0
    assert _filas_locales(conn) == [("F1", 1, 10.0), ("F2", 1, 10.0)]


def test_aplicar_no_borra_folios_vecinos_del_mismo_dia(tmp_db):
    """El diff es por folio: un folio ausente de la API no puede borrarse."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1"), _fila("F2")])
    api = _FakeApi(
        ["F1", "F3"],
        [_fila("F1"), _fila("F3"), _fila("F4")],
        dias=[{"dia": "2026-10-01", "filas": 3, "soles": 30.0}],
    )
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["filas"] == 3
    assert [r[0] for r in _filas_locales(conn)] == ["F1", "F2", "F3", "F4"]


def test_aplicar_descarta_filas_sin_folio(tmp_db):
    """Sin folio_unico no hay replace idempotente: se descartan, no se duplican."""
    conn = tmp_db.get_conn()
    api = _FakeApi(["F1"], [_fila("F1"), {"soles": 5.0, "fecha_orig": "2026-10-01"}])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["filas"] == 1 and res["descartadas_sin_folio"] == 1
    assert _filas_locales(conn) == [("F1", 1, 10.0)]


def test_aplicar_agrupa_por_dia_y_registra_checksums(tmp_db):
    conn = tmp_db.get_conn()
    filas = [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02")]
    res = SyncAPI(_FakeApi(["F1", "F2"], filas)).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["dias"] == ["2026-10-01", "2026-10-02"]
    assert res["meses"] == ["2026-10"]
    assert tmp_db.day_checksums_rango(conn, "2026-10-01", "2026-10-02") == {
        "2026-10-01": {"filas": 1, "soles": 10.0},
        "2026-10-02": {"filas": 1, "soles": 10.0},
    }
    ok, drifts, sin = tmp_db.verify_integrity(conn)
    assert ok and drifts == [] and sin == []
    assert tuple(
        conn.execute("SELECT estado, filas_subidas FROM sync_log WHERE tipo='sync_api'").fetchone()
    ) == ("ok", 2)


def test_aplicar_registra_error_y_relanza(tmp_db):
    conn = tmp_db.get_conn()
    api = _FakeApi(["F1"], [_fila("F1")])
    boom = lambda *a, **k: (_ for _ in ()).throw(VentaAPIError("se cayo la API"))
    api.ventas_by_folios = boom
    with pytest.raises(VentaAPIError):
        SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn, usar_dias=False)
    assert tuple(conn.execute("SELECT estado FROM sync_log WHERE tipo='sync_api'").fetchone()) == (
        "error",
    )


def test_aplicar_no_toca_dias_donde_local_va_adelantado(tmp_db):
    """Si el API esta atrasado no se reescriben filas locales que ya estan bien.

    Es el caso real del despliegue: el snapshot del servidor es mas viejo que la
    ultima captura local, asi que local tiene mas filas que el API en ese dia.
    """
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1"), _fila("FZ")])
    api = _FakeApi(["F1"], [_fila("F1")])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-31", conn=conn)
    assert res["estado"] == "sin_cambios" and res["filas"] == 0
    assert res["dias_desfasados"] == []
    assert res["dias_local_adelantado"] == ["2026-10-01"]
    assert api.pedidos_dias == []
    assert [f for f, _, _ in _filas_locales(conn)] == ["F1", "FZ"]


def test_aplicar_separa_falta_de_local_adelantado(tmp_db):
    """Un dia que falta y otro donde local va delante, en la misma corrida."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(
        conn,
        [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02"), _fila("F5", "2026-10-02")],
    )
    api = _FakeApi(
        ["F1", "F3"],
        [_fila("F3", "2026-10-03"), _fila("F4", "2026-10-03")],
        dias=[
            {"dia": "2026-10-01", "filas": 1, "soles": 10.0},
            {"dia": "2026-10-02", "filas": 1, "soles": 10.0},
            {"dia": "2026-10-03", "filas": 2, "soles": 20.0},
        ],
    )
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-03", conn=conn)
    assert res["dias_desfasados"] == ["2026-10-03"]
    assert res["dias_local_adelantado"] == ["2026-10-02"]
    assert api.pedidos_dias == ["2026-10-03"]
    assert sorted(f for f, _, _ in _filas_locales(conn)) == ["F1", "F2", "F3", "F4", "F5"]


def test_dias_desfasados_ignora_dias_adelantados(tmp_db):
    """Si local tiene mas que el API, el dia no entra en el plan de sync."""
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02")])
    dias_api = [
        {"dia": "2026-10-01", "filas": 1, "soles": 5.0},
        {"dia": "2026-10-02", "filas": 0, "soles": 0.0},
        {"dia": "2026-10-03", "filas": 2, "soles": 20.0},
    ]
    assert SyncAPI.dias_desfasados("2026-10-01", "2026-10-03", dias_api, conn) == ["2026-10-03"]


def test_aplicar_reporta_dias_converjados(tmp_db):
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", "2026-10-01")])
    api = _FakeApi(["F1", "F2"], [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02")])
    res = SyncAPI(api).aplicar("2026-10-01", "2026-10-02", conn=conn)
    assert res["dias_desfasados"] == ["2026-10-02"]
    assert res["dias_converjados"] == 1 and res["dias_sin_converger"] == 0


def test_dias_desfasados_compara_filas_y_soles(tmp_db):
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", "2026-10-01"), _fila("F2", "2026-10-02")])
    dias_api = [
        {"dia": "2026-10-01", "filas": 1, "soles": 10.0},
        {"dia": "2026-10-02", "filas": 3, "soles": 30.0},
        {"dia": "2026-10-03", "filas": 5, "soles": 50.0},
    ]
    assert SyncAPI.dias_desfasados("2026-10-01", "2026-10-03", dias_api, conn) == [
        "2026-10-02",
        "2026-10-03",
    ]


def test_dias_desfasados_tolera_redondeo_de_soles(tmp_db):
    conn = tmp_db.get_conn()
    tmp_db.insert_ventas(conn, [_fila("F1", "2026-10-01")])
    dias_api = [{"dia": "2026-10-01", "filas": 1, "soles": 10.004}]
    assert SyncAPI.dias_desfasados("2026-10-01", "2026-10-01", dias_api, conn) == []


def test_ventas_por_dia_filtra_por_fecha(monkeypatch):
    cli = _cli(
        monkeypatch,
        [("/api/data/ventas", _FakeResp(200, {"datos": [{"folio_unico": "F1"}], "filas": 1}))],
    )
    filas = cli.ventas_por_dia("2026-10-01", limit=500, offset=10)
    assert filas == [{"folio_unico": "F1"}]


def test_replace_folios_exige_folio_unico(tmp_db):
    with pytest.raises(ValueError):
        tmp_db.replace_folios(tmp_db.get_conn(), [{"soles": 1.0}])


def test_admin_refresh_devuelve_trigger(monkeypatch):
    cli = _cli(
        monkeypatch,
        [
            (
                "/api/admin/refresh",
                _FakeResp(200, {"status": "triggered", "timestamp": "2026-10-02T22:00:00Z"}),
            ),
        ],
    )
    resp = cli.admin_refresh()
    assert resp["status"] == "triggered"


def test_admin_refresh_status_devuelve_estado(monkeypatch):
    cli = _cli(
        monkeypatch,
        [
            (
                "/api/admin/refresh-status",
                _FakeResp(200, {"status": "ok", "duration_sec": 365.6}),
            ),
        ],
    )
    resp = cli.admin_refresh_status()
    assert resp["status"] == "ok"


def test_admin_refresh_busy_no_lanza(monkeypatch):
    cli = _cli(
        monkeypatch,
        [("/api/admin/refresh", _FakeResp(200, {"status": "busy"}))],
    )
    resp = cli.admin_refresh()
    assert resp["status"] == "busy"
