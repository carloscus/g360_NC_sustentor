"""Despertar la API de ventas en WSL + el flujo de "Actualizar desde API".

El server corre en WSL y muere con la distro, asi que la UI tiene que
despertarlo antes de sincronizar. `_correr_sync_api` hace exactamente eso:
health -> (arrancar si caida) -> token -> sync, con el boton siempre
rehabilitado.

`_asegurar_api` y `_sync_por_defecto` son las dependencias reales que
`_correr_sync_api` usa si no se le pasan: los tests las inyectan.
"""

import socket
import sys
from types import SimpleNamespace

import httpx
import pytest

from src.core import api_wake
from src.core.api_wake import _ruta_wsl, asegurar_api, proyecto_ventas_api
from src.ui.view_panels import _correr_sync_api, _msgs_sync_api, _rango_sync


class TestRutaWsl:
    def test_convierte_c_mauscula(self):
        assert _ruta_wsl("C:\\Users\\ccusi\\x") == "/mnt/c/Users/ccusi/x"

    def test_ya_posix_no_rompe(self, tmp_path):
        p = tmp_path / "deploy"
        assert _ruta_wsl(p).startswith("/mnt/")


class TestProyectoVentasApi:
    def test_usa_env_si_existe(self, tmp_path, monkeypatch):
        d = tmp_path / "g360-ventas-api"
        (d / "deploy").mkdir(parents=True)
        (d / "deploy" / "start_api.sh").write_text("#!/bin/bash\n")
        monkeypatch.setenv("G360_VENTAS_API_DIR", str(d))
        assert proyecto_ventas_api() == d

    def test_env_invalida_devuelve_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("G360_VENTAS_API_DIR", str(tmp_path / "nope"))
        assert proyecto_ventas_api() is None

    def test_busca_hermano_sin_env(self, tmp_path, monkeypatch):
        d = tmp_path / "g360-ventas-api"
        (d / "deploy").mkdir(parents=True)
        (d / "deploy" / "start_api.sh").write_text("#!/bin/bash\n")
        monkeypatch.delenv("G360_VENTAS_API_DIR", raising=False)
        fake = tmp_path / "erp" / "src" / "core" / "api_wake.py"
        fake.parent.mkdir(parents=True)
        fake.write_text("")
        monkeypatch.setattr(api_wake, "__file__", str(fake))
        assert proyecto_ventas_api() == d


def _fake_health(*_a, **_k):
    return {"db": "/home/x/historial.db", "status": "ok", "filas": 100}


class TestHealthReal:
    """`_health` pega a /api/health y exige status=ok.

    Sin el path exacto, la raiz responde 200 con el indice de rutas y el wake
    reportaba "API viva" con la API realmente caida.
    """

    def _get(self, monkeypatch, cuerpo, code=200):
        pedido = {}

        class R:
            status_code = code

            def json(self):
                return cuerpo

        def fake_get(url, timeout=None):
            pedido["url"] = url
            return R()

        monkeypatch.setattr(httpx, "get", fake_get)
        return pedido

    def test_consulta_el_path_de_health(self, monkeypatch):
        p = self._get(monkeypatch, {"db": "/x", "status": "ok"})
        assert api_wake._health("http://127.0.0.1:8090", 5)["db"] == "/x"
        assert p["url"] == "http://127.0.0.1:8090/api/health"

    def test_tolera_slash_final(self, monkeypatch):
        self._get(monkeypatch, {"status": "ok"})
        assert api_wake._health("http://127.0.0.1:8090/", 5) is not None

    def test_indice_de_rutas_no_cuenta_como_viva(self, monkeypatch):
        """La raiz devuelve 200 sin status=ok: es un falso positivo."""
        cuerpo = {"api": "g360-ventas-api", "rutas": ["/api/health", "/api/stats"]}
        self._get(monkeypatch, cuerpo)
        assert api_wake._health("http://127.0.0.1:8090", 5) is None

    def test_no_200_es_none(self, monkeypatch):
        self._get(monkeypatch, {"status": "ok"}, code=503)
        assert api_wake._health("http://127.0.0.1:8090", 5) is None

    def test_json_invalido_es_none(self, monkeypatch):
        def boom(url, timeout=None):
            raise ValueError("no json")

        monkeypatch.setattr(httpx, "get", boom)
        assert api_wake._health("http://x", 5) is None


class TestPuertoEscucha:
    def test_puerto_cerrado_da_false(self):
        # puerto efimero que casi seguro esta libre
        assert api_wake.puerto_escucha("127.0.0.1", 9) is False

    def test_puerto_abierto_da_true(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            assert api_wake.puerto_escucha("127.0.0.1", srv.getsockname()[1]) is True
        finally:
            srv.close()


class TestLanzarForwarder:
    def test_falta_el_script_da_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="no existe"):
            api_wake.lanzar_forwarder(tmp_path)

    def test_levanta_el_interprete_actual(self, tmp_path, monkeypatch):
        (tmp_path / "deploy").mkdir()
        script = tmp_path / "deploy" / "forwarder.py"
        script.write_text("# stub\n")
        popped = {}
        monkeypatch.setattr(
            api_wake.subprocess, "Popen", lambda *a, **k: popped.setdefault("args", a)
        )
        api_wake.lanzar_forwarder(tmp_path)
        assert popped["args"][0][0] == sys.executable
        assert str(script) in popped["args"][0]
        assert "8090" in popped["args"][0]


class TestLanzarEnWsl:
    def test_arma_el_comando_wsl(self, tmp_path, monkeypatch):
        popped = {}
        monkeypatch.setattr(api_wake.shutil, "which", lambda n: "C:/wsl.exe")
        monkeypatch.setattr(
            api_wake.subprocess, "Popen", lambda *a, **k: popped.setdefault("args", a)
        )
        detalle = api_wake._lanzar_en_wsl(tmp_path)
        args = popped["args"][0]
        assert args[0] == "wsl"
        assert "-d" in args and "Ubuntu" in args
        assert args[-2] == api_wake._ruta_wsl(tmp_path / "deploy" / "start_api.sh")
        assert ":\\" not in args[-2] and ":/" not in args[-2], "el path debe ser POSIX para WSL"
        assert args[-1] == "8091", "el puerto del upstream en WSL es 8091"
        assert "start_api.sh" in detalle


class TestAsegurarApi:
    def test_ya_arriba_no_arranca(self, monkeypatch):
        monkeypatch.setattr(api_wake, "_health", _fake_health)
        llamo = []
        r = asegurar_api(lanzar=lambda p: llamo.append(p) or "x", proyecto="P")
        assert r["ok"] is True
        assert r["arrancada"] is False
        assert not llamo, "no debe arrancar si ya responde"

    def test_caida_arranca_y_espera(self, monkeypatch, tmp_path):
        """1er health falla, el 2do ok: se lanza y se devuelve arrancada=True."""
        calls = {"n": 0}

        def health(*_a, **_k):
            calls["n"] += 1
            return None if calls["n"] == 1 else _fake_health()

        lanzadas = []
        monkeypatch.setattr(api_wake, "_health", health)
        r = asegurar_api(
            proyecto=tmp_path,
            lanzar=lambda p: lanzadas.append(p) or "wsl -d Ubuntu",
            dormir=lambda _s: None,
            escucha=lambda: True,
        )
        assert r["ok"] is True
        assert r["arrancada"] is True
        assert len(lanzadas) == 1

    def test_forwarder_caido_se_relanza(self, monkeypatch, tmp_path):
        """WSL arriba pero sin quien escuche en 8090: hay que levantar los dos."""
        (tmp_path / "deploy").mkdir(parents=True, exist_ok=True)
        (tmp_path / "deploy" / "forwarder.py").write_text("")
        llamadas = []
        monkeypatch.setattr(api_wake, "_health", lambda *a, **k: None)
        r = asegurar_api(
            proyecto=tmp_path,
            lanzar=lambda p: llamadas.append("api") or "api",
            lanzar_fwd=lambda p: llamadas.append("fwd") or "forwarder :8090",
            dormir=lambda _s: None,
            escucha=lambda: False,
            espera_max_s=1.5,
        )
        assert llamadas == ["fwd", "api"], "el forwarder va primero: 8090 debe escuchar ya"
        assert "forwarder :8090" in r["detalle"], r["detalle"]

    def test_no_responde_da_error_accionado(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_wake, "_health", lambda *a, **k: None)
        r = asegurar_api(
            proyecto=tmp_path,
            lanzar=lambda p: "wsl -d Ubuntu",
            dormir=lambda _s: None,
            escucha=lambda: True,
            espera_max_s=3.0,
        )
        assert r["ok"] is False
        assert "wsl -l -v" in r["detalle"], "el error debe decir como diagnosticar"
        assert "api.log" in r["detalle"]

    def test_sin_repo_no_intenta_arrancar(self, monkeypatch):
        monkeypatch.setattr(api_wake, "_health", lambda *a, **k: None)
        monkeypatch.setattr(api_wake, "proyecto_ventas_api", lambda: None)
        r = asegurar_api(lanzar=lambda p: pytest.fail("no debe lanzar"))
        assert r["ok"] is False
        assert "G360_VENTAS_API_DIR" in r["detalle"]


class TestMensajesSync:
    def test_error_excepcion(self):
        t, ok = _msgs_sync_api(ex=RuntimeError("boom"))
        assert ok is False and "boom" in t

    def test_sin_token(self):
        t, ok = _msgs_sync_api(sin_token=True)
        assert ok is False and "token" in t.lower()

    def test_api_caida(self):
        t, ok = _msgs_sync_api(wake={"ok": False, "detalle": "sin upstream"})
        assert ok is False and "no disponible" in t

    def test_sin_cambios_muestra_rango(self):
        t, ok = _msgs_sync_api(
            res={"estado": "sin_cambios", "desde": "a", "hasta": "b", "segundos": 1.0}
        )
        assert ok is True and "sin cambios" in t and "a" in t

    def test_ok_con_filas(self):
        t, ok = _msgs_sync_api(
            res={
                "estado": "ok",
                "filas": 1500,
                "dias": ["x"],
                "dias_desfasados": ["x"],
                "folios_faltantes": 3,
                "segundos": 2.0,
            }
        )
        assert ok is True and "1,500" in t and "3 folios nuevos" in t

    def test_menciona_despierte(self):
        t, _ = _msgs_sync_api(
            wake={"ok": True, "arrancada": True, "segundos": 4.0},
            res={"estado": "sin_cambios", "desde": "a", "hasta": "b", "segundos": 1.0},
        )
        assert "despertada" in t

    def test_avisa_local_adelantada(self):
        t, _ = _msgs_sync_api(
            res={
                "estado": "ok",
                "filas": 5,
                "dias": ["x"],
                "dias_desfasados": ["x"],
                "dias_local_adelantado": ["y", "z"],
                "folios_faltantes": 1,
                "segundos": 1.0,
            }
        )
        assert "2 días" in t and "adelantada" in t


class TestRangoSync:
    def test_ventana_90_dias_por_defecto(self, monkeypatch):
        monkeypatch.setattr("src.core.ventas_db.db_health", lambda: {"fecha_max": "2026-10-02"})
        desde, hasta = _rango_sync()
        assert hasta == "2026-10-02"
        assert desde == "2026-07-04", "90 días atrás desde 2026-10-02"

    def test_db_vacia_usa_hoy(self, monkeypatch):
        monkeypatch.setattr("src.core.ventas_db.db_health", lambda: {"fecha_max": None})
        _d, h = _rango_sync()
        assert len(h) == 10 and h[4] == "-"


class _Page:
    def __init__(self):
        self.updates = 0

    def update(self):
        self.updates += 1


def _harness():
    h = SimpleNamespace()
    h.status = SimpleNamespace(value="", color=None)
    h.btn = SimpleNamespace(disabled=False)
    h.app = SimpleNamespace(
        G360_ACCENT="accent", G360_SUCCESS="ok", G360_ERROR="err", G360_WARNING="warn"
    )
    h.page = _Page()
    h.busy = []
    h.logs = []
    return h


def _correr(h, **kw):
    base = dict(
        status=h.status,
        btn_sync=h.btn,
        app=h.app,
        page=h.page,
        api_url="http://127.0.0.1:8090",
        set_busy=lambda v: h.busy.append(v),
        registrar=h.logs.append,
        token_valido="tok-de-prueba",
    )
    base.update(kw)
    t = _correr_sync_api(**base)
    t.join(timeout=15)
    assert not t.is_alive(), "el thread del sync no termino"
    return h


def _credenciales(monkeypatch, token_inicial="viejo"):
    """Mockea CaptureService con un token que pasa a 'nuevo' tras el login."""
    logins = []

    def api_token():
        return "nuevo" if logins else token_inicial

    monkeypatch.setattr(
        "src.core.capture_service.CaptureService.api_token", staticmethod(api_token)
    )
    monkeypatch.setattr(
        "src.core.capture_service.CaptureService.credentials", staticmethod(lambda: ("u", "p"))
    )
    monkeypatch.setattr(
        "src.core.capture_service.CaptureService.refresh_api_token_best_effort",
        staticmethod(lambda u, p: logins.append((u, p))),
    )
    return logins


class _Cli401:
    """Cliente que responde 401 mientras el token sea 'viejo'."""

    def __init__(self):
        self.tok = ""

    def set_token(self, t):
        self.tok = t

    def health(self):
        from src.core.ventas_api_client import TokenInvalidoError

        if self.tok == "viejo":
            raise TokenInvalidoError("401 en /api/health", status=401)

    def close(self):
        pass


class TestRenovacionToken:
    def test_token_caduco_se_renueva_antes_de_bajar(self, monkeypatch):
        """El cacheado da 401 en el health: login otra vez y el sync corre una vez."""
        logins = _credenciales(monkeypatch)
        h = _correr(
            _harness(),
            wake=lambda url: {"ok": True, "arrancada": False},
            token_valido=None,
            cliente=lambda: _Cli401(),
            sync=lambda c, d, h: {
                "estado": "sin_cambios",
                "desde": d,
                "hasta": h,
                "segundos": 1.0,
            },
            rango=lambda: ("a", "b"),
        )
        assert logins, "debe intentar login otra vez"
        assert "sin cambios" in h.status.value
        assert h.busy == [True, False], "el sync debe correr una sola vez"

    def test_401_en_la_descarga_reintenta_una_vez(self, monkeypatch):
        """El token era valido en el health pero caduca al descargar: un reintento."""
        logins = _credenciales(monkeypatch)
        cli = _Cli401()
        cli.health = lambda: None  # el health pasa: el token aun sirve
        intentos = []

        def sync(c, d, h):
            intentos.append(c.tok)
            if len(intentos) == 1:
                from src.core.ventas_api_client import TokenInvalidoError

                raise TokenInvalidoError("401 en /api/folios", status=401)
            return {"estado": "sin_cambios", "desde": d, "hasta": h, "segundos": 1.0}

        h = _correr(
            _harness(),
            wake=lambda url: {"ok": True, "arrancada": False},
            token_valido=None,
            cliente=lambda: cli,
            sync=sync,
            rango=lambda: ("a", "b"),
        )
        assert intentos == ["viejo", "nuevo"], "un reintento, con el token renovado"
        assert logins
        assert "sin cambios" in h.status.value


class TestCierreSnackbar:
    """`al_final` debe correr en TODOS los caminos, incluso los que cortan temprano.

    Sin esto el usuario ve el overlay de carga desaparecer sin saber que paso.
    """

    def _correr(self, **kw):
        vistos = []
        base = dict(
            status=SimpleNamespace(value="", color=None),
            btn_sync=SimpleNamespace(disabled=False),
            app=SimpleNamespace(
                G360_ACCENT="accent", G360_SUCCESS="ok", G360_ERROR="err", G360_WARNING="warn"
            ),
            page=_Page(),
            api_url="http://127.0.0.1:8090",
            set_busy=lambda v: None,
            token_valido="tok",
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            rango=lambda: ("a", "b"),
            al_final=lambda t, ok: vistos.append((t, ok)),
        )
        base.update(kw)
        t = _correr_sync_api(**base)
        t.join(timeout=15)
        assert not t.is_alive()
        return vistos

    def test_avisa_cuando_api_cae(self):
        vistos = self._correr(
            wake=lambda url: {"ok": False, "detalle": "sin upstream"},
            cliente=lambda: pytest.fail("no debe sincronizar"),
        )
        assert len(vistos) == 1
        assert vistos[0][1] is False
        assert "no disponible" in vistos[0][0]

    def test_avisa_cuando_no_hay_token(self):
        monkey = {"api_token": ""}
        vistos = self._correr(
            wake=lambda url: {"ok": True, "arrancada": False},
            token_valido="",
            cliente=lambda: pytest.fail("no debe sincronizar"),
        )
        assert len(vistos) == 1 and vistos[0][1] is False

    def test_avisa_cuando_sync_explota(self):
        def boom(c, d, h):
            raise RuntimeError("db bloqueada")

        vistos = self._correr(
            wake=lambda url: {"ok": True, "arrancada": False},
            sync=boom,
        )
        assert len(vistos) == 1
        assert vistos[0][1] is False and "db bloqueada" in vistos[0][0]

    def test_avisa_ok(self):
        vistos = self._correr(
            wake=lambda url: {"ok": True, "arrancada": False},
            sync=lambda c, d, h: {
                "estado": "sin_cambios",
                "desde": d,
                "hasta": h,
                "segundos": 1.0,
            },
        )
        assert len(vistos) == 1 and vistos[0][1] is True

    def test_sin_al_final_no_revienta(self):
        """El panel de intranet no pasa al_final: tiene que seguir funcionando."""
        t = _correr_sync_api(
            status=SimpleNamespace(value="", color=None),
            btn_sync=SimpleNamespace(disabled=False),
            app=SimpleNamespace(
                G360_ACCENT="a", G360_SUCCESS="ok", G360_ERROR="err", G360_WARNING="w"
            ),
            page=_Page(),
            api_url="http://x",
            set_busy=lambda v: None,
            token_valido="t",
            wake=lambda url: {"ok": False, "detalle": "x"},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            rango=lambda: ("a", "b"),
        )
        t.join(timeout=15)
        assert not t.is_alive()

    def test_al_final_roto_no_rompe_el_flujo(self):
        def malo(t, ok):
            raise RuntimeError("snackbar roto")

        t = _correr_sync_api(
            status=SimpleNamespace(value="", color=None),
            btn_sync=SimpleNamespace(disabled=True),
            app=SimpleNamespace(
                G360_ACCENT="a", G360_SUCCESS="ok", G360_ERROR="err", G360_WARNING="w"
            ),
            page=_Page(),
            api_url="http://x",
            set_busy=lambda v: None,
            token_valido="t",
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            rango=lambda: ("a", "b"),
            sync=lambda c, d, h: {"estado": "sin_cambios", "desde": d, "hasta": h, "segundos": 1.0},
            al_final=malo,
        )
        t.join(timeout=15)
        assert not t.is_alive()


class TestSetBusyNoMuestraFalse:
    """`set_busy` debe traducir el booleano, no colarlo como mensaje.

    Regresion: se pasaba `self.app.show_loading` directo a `_correr_sync_api`,
    que llama set_busy(True/False). show_loading espera un string, asi que
    renderizaba el overlay con el texto literal "False".
    """

    def test_el_overlay_no_muestra_false(self):
        """El modal maneja su propio overlay; la aplicación jamás recibe `False`.

        Regresión: antes el card pasaba `self.app.show_loading` directo como
        `set_busy`, y el booleano "False" quedaba en pantalla. Ahora el flujo
        vive dentro de `ApiSyncModal` y la app solo ve mensajes explícitos.
        """
        from src.ui.components.api_sync_modal import ApiSyncModal
        from src.ui.view_panels import _ViewPanels

        # El canal ya no acepta un setter crudo: el Modal traduce al overlay.
        v = _ViewPanels.__new__(_ViewPanels)
        assert hasattr(ApiSyncModal, "open")
        assert not hasattr(v, "_sincronizar_desde_api")
        assert not hasattr(v, "_mostrar_resultado_api")


class TestBotonCard:
    """El boton provisional debe existir en la card y ser alcanzable.

    Regresion: el boton de la API vivia en `_panel_intranet`, que no construye
    ningun control de la app (`_gestionar_datos` no tiene callers), asi que era
    inalcanzable. Ahora vive en la card de "Historial local".
    """

    def _construir(self):
        from src.ui.view_panels import _ViewPanels

        v = _ViewPanels.__new__(_ViewPanels)
        v.app = SimpleNamespace()
        return v

    def test_el_handler_existe(self):
        """Los handlers viejos se borraron; el canal único es el modal."""
        from src.ui.components.api_sync_modal import ApiSyncModal
        from src.ui.view_panels import _ViewPanels

        assert hasattr(ApiSyncModal, "open") and callable(ApiSyncModal.open)
        # Y de la clase vieja solo quedan los métodos del panel.
        assert callable(_ViewPanels._refrescar_card_db)

    def test_la_card_registra_el_boton(self):
        import flet as ft

        from src.ui.view_panels import _ViewPanels

        v = _ViewPanels.__new__(_ViewPanels)
        v.app = SimpleNamespace()
        creado = {}

        def fake_ghost(txt, icon=None, on_click=None):
            creado[txt] = SimpleNamespace(text=txt, on_click=on_click, visible=True)
            return creado[txt]

        def fake_card(*a, **k):
            return ft.Container()

        monkey = ft
        with _patch_ghost(fake_ghost):
            v._construir_card_db()

        # El modal unificado reemplazó al botón provisional: "Actualizar hoy"
        # ahora abre ApiSyncModal en vez de abrir login XLS directo.
        assert "Actualizar hoy" in creado, f"botones: {list(creado)}"
        assert creado["Actualizar hoy"].on_click == v._actualizar_hoy


class _patch_ghost:
    """Reemplaza G360Theme.ghost_button durante el build de la card."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        from src.ui import view_panels

        self._orig = view_panels.G360Theme.ghost_button
        view_panels.G360Theme.ghost_button = self.fake
        return self

    def __exit__(self, *a):
        from src.ui import view_panels

        view_panels.G360Theme.ghost_button = self._orig


class TestFlujoSyncApi:
    def test_api_arriba_sincroniza(self, monkeypatch):
        h = _harness()
        cliente = SimpleNamespace(set_token=lambda t: None, close=lambda: None)
        res = {
            "estado": "ok",
            "filas": 10,
            "dias": ["d"],
            "dias_desfasados": ["d"],
            "folios_faltantes": 1,
            "segundos": 1.0,
            "modo": "dias",
        }
        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: cliente,
            sync=lambda c, d, hst: res,
            rango=lambda: ("2026-09-01", "2026-10-02"),
        )
        assert "✓ API" in h.status.value
        assert h.btn.disabled is False

    def test_api_caida_muestra_error(self, monkeypatch):
        h = _harness()
        h = _correr(
            h,
            wake=lambda url: {"ok": False, "detalle": "sin upstream"},
            cliente=lambda: pytest.fail("no debe crear cliente si la API no esta"),
            sync=lambda *a: pytest.fail("no debe sincronizar"),
        )
        assert "no disponible" in h.status.value
        assert h.status.color == "err"
        assert h.btn.disabled is False, "el boton debe quedar habilitado siempre"

    def test_boton_se_habilita_aunque_sync_explote(self, monkeypatch):
        h = _harness()

        def boom(c, d, hst):
            raise RuntimeError("db bloqueada")

        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            sync=boom,
            rango=lambda: ("a", "b"),
        )
        assert "db bloqueada" in h.status.value
        assert h.btn.disabled is False, "una excepcion no debe dejar el boton muerto"

    def test_registra_wake_en_log(self, monkeypatch):
        h = _harness()
        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": True, "segundos": 4.0},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            sync=lambda c, d, hst: {
                "estado": "sin_cambios",
                "desde": "a",
                "hasta": "b",
                "segundos": 1.0,
            },
            rango=lambda: ("a", "b"),
        )
        assert any("despertada" in m for m in h.logs)

    def test_al_actualizar_solo_si_hubo_filas(self, monkeypatch):
        h = _harness()
        calls = []
        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            sync=lambda c, d, hst: {
                "estado": "sin_cambios",
                "desde": "a",
                "hasta": "b",
                "segundos": 1.0,
            },
            rango=lambda: ("a", "b"),
            al_actualizar=calls.append,
        )
        assert not calls, "sin cambios no debe tocar la UI de descargas"

    def test_al_actualizar_con_filas(self, monkeypatch):
        h = _harness()
        calls = []
        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            sync=lambda c, d, hst: {
                "estado": "ok",
                "filas": 10,
                "dias": ["d"],
                "dias_desfasados": ["d"],
                "folios_faltantes": 1,
                "segundos": 1.0,
                "modo": "dias",
            },
            rango=lambda: ("a", "b"),
            al_actualizar=calls.append,
        )
        assert len(calls) == 1, "con filas nuevas debe refrescar la UI"

    def test_sin_token_no_sincroniza(self, monkeypatch):
        """Sin token cacheado ni credenciales: avisa y no intenta sincronizar."""
        monkeypatch.setattr(
            "src.core.capture_service.CaptureService.api_token", staticmethod(lambda: None)
        )
        monkeypatch.setattr(
            "src.core.capture_service.CaptureService.credentials",
            staticmethod(lambda: (None, None)),
        )
        h = _harness()
        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            token_valido=None,
            cliente=lambda: pytest.fail("no debe crear cliente sin token"),
            sync=lambda *a: pytest.fail("no debe sincronizar"),
            rango=lambda: ("a", "b"),
        )
        assert "token" in h.status.value.lower()
        assert h.status.color == "err"
        assert h.btn.disabled is False

    def test_401_repetido_no_insiste(self, monkeypatch):
        """Si el 401 se repite, error claro: no hay token que sirve."""
        monkeypatch.setattr(
            "src.core.capture_service.CaptureService.api_token", staticmethod(lambda: "t")
        )
        monkeypatch.setattr(
            "src.core.capture_service.CaptureService.credentials",
            staticmethod(lambda: (None, None)),
        )

        class Cli:
            def __init__(self):
                self.tok = ""

            def set_token(self, t):
                self.tok = t

            def health(self):
                from src.core.ventas_api_client import TokenInvalidoError

                raise TokenInvalidoError("401", status=401)

            def close(self):
                pass

        h = _correr(
            _harness(),
            wake=lambda url: {"ok": True, "arrancada": False},
            token_valido=None,
            cliente=lambda: Cli(),
            sync=lambda c, d, h: pytest.fail("no debe llegar al sync"),
            rango=lambda: ("a", "b"),
        )
        assert h.status.color == "err"
        assert "token" in h.status.value.lower()
        assert h.btn.disabled is False

    def test_busy_se_limpia_si_sync_falla(self, monkeypatch):
        h = _harness()

        def boom(c, d, hst):
            raise RuntimeError("x")

        h = _correr(
            h,
            wake=lambda url: {"ok": True, "arrancada": False},
            cliente=lambda: SimpleNamespace(set_token=lambda t: None, close=lambda: None),
            sync=boom,
            rango=lambda: ("a", "b"),
        )
        assert h.busy[-1] is False, "el busy indicator debe limpiarse siempre"
