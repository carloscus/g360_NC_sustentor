"""Flujo de export de cartucho en la UI: alertas y boton siempre habilitado.

La UI vive anidada en _abrir_config_db, asi que el flujo esta en
_correr_export_cartucho (modulo nivel, con `exportar` inyectado) y los textos
en _msgs_cartucho. Asi lo que se testea es el codigo real: que se avise cada
etapa, que se nombre la carpeta de destino, y sobre todo que el boton quede
habilitado SIEMPRE (un error no puede dejarlo muerto).
"""

from types import SimpleNamespace

import flet as ft

from src.ui.view_panels import (
    _cartucho_previas,
    _correr_export_cartucho,
    _msgs_cartucho,
)


class _Page:
    def __init__(self, on_update):
        self._on_update = on_update

    def update(self):
        self._on_update()


class _Harness:
    """app/page minimos + registro de los avisos emitidos."""

    def __init__(self):
        self.status = ft.Text("")
        self.btn = ft.ElevatedButton("Exportar")
        self.snacks: list[tuple[str, object]] = []
        self.updates = 0
        self.app = SimpleNamespace(
            G360_ACCENT="accent",
            G360_SUCCESS="success",
            G360_ERROR="error",
            G360_WARNING="warning",
            show_snackbar=lambda m, c=None: self.snacks.append((m, c)),
        )
        self.page = _Page(self._contar_update)

    def _contar_update(self):
        self.updates += 1

    def correr(self, carpeta, exportar, **kw):
        t = _correr_export_cartucho(
            carpeta_export=carpeta,
            status=self.status,
            btn_export=self.btn,
            app=self.app,
            page=self.page,
            exportar=exportar,
            **kw,
        )
        t.join(timeout=10)
        assert not t.is_alive(), "el thread del export no termino"
        return self


class TestMensajesCartucho:
    def test_creando_nombra_la_carpeta(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        texto, aviso = _msgs_cartucho(exp)
        assert "Creando cartucho" in texto
        assert str(exp) in texto
        # el aviso tambien lleva la carpeta (via el resumen de previas)
        assert "Creando cartucho" in aviso

    def test_creando_sin_previas(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        _, aviso = _msgs_cartucho(exp)
        assert "primero" in aviso

    def test_creando_con_previas_las_cuenta(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        (exp / "cartucho-20260101-000000.zip").write_bytes(b"x" * 2048)
        (exp / "cartucho-20260102-000000.zip").write_bytes(b"x" * 1024)
        _, aviso = _msgs_cartucho(exp)
        assert "2 cartucho(s) anterior(es)" in aviso
        assert "en la misma carpeta" in aviso

    def test_creado_reporta_ruta_filas_y_tamano(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        zip_path = exp / "cartucho-20260930-101112.zip"
        r = {"zip": str(zip_path), "ventas_filas": 2812871, "zip_mb": 655}
        texto, aviso = _msgs_cartucho(exp, r=r)
        assert "Cartucho creado" in texto
        assert str(zip_path) in texto
        assert "2,812,871" in texto  # separador de miles
        assert "655 MB" in texto
        # el snackbar lleva la carpeta para abrirla sin buscar
        assert str(exp) in aviso

    def test_error_generico(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        texto, aviso = _msgs_cartucho(exp, ex=RuntimeError("DB no existe"))
        assert "No se pudo crear el cartucho" in texto
        assert "DB no existe" in texto
        assert "No se pudo crear el cartucho" in aviso

    def test_error_nombre_duplicado_es_otro_mensaje(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        texto, _ = _msgs_cartucho(exp, ex=FileExistsError("ya existe: x"))
        assert "Ya existe un cartucho" in texto
        # no debe pedir perdon generico: es un caso recuperable con retry
        assert "No se pudo crear" not in texto

    def test_previas_ordena_mas_nuevo_primero(self, tmp_path):
        import os

        exp = tmp_path / "export"
        exp.mkdir()
        viejo = exp / "cartucho-20260101-000000.zip"
        nuevo = exp / "cartucho-20260930-101112.zip"
        for p in (viejo, nuevo):
            p.write_bytes(b"x")
        os.utime(viejo, (1_000_000, 1_000_000))
        os.utime(nuevo, (2_000_000, 2_000_000))
        assert _cartucho_previas(exp)[0] == nuevo

    def test_previas_ignora_no_zip(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        (exp / "cartucho-20260930-101112.zip").write_bytes(b"x")
        (exp / "notas.txt").write_text("hola")
        (exp / "cartucho-20260930-101112").mkdir()  # carpeta, no archivo
        assert [p.name for p in _cartucho_previas(exp)] == ["cartucho-20260930-101112.zip"]


class TestFlujoExportIncremental:
    """El rango llega al `exportar` inyectado, y solo cuando hay rango.

    El cartucho completo es "sin argumentos": los callables de prueba (y el
    contrato del export completo) no llevan kwargs.
    """

    def test_el_rango_se_pasa_al_export(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        recibido = {}

        def _export(desde=None, hasta=None):
            recibido.update(desde=desde, hasta=hasta)
            return {"zip": str(exp / "cartucho-x-inc-20260201.zip"), "ventas_filas": 10}

        _Harness().correr(exp, _export, desde="2026-02-01")
        assert recibido == {"desde": "2026-02-01", "hasta": None}

    def test_el_completo_no_pasa_kwargs(self, tmp_path):
        """Sin rango no se pasa nada: el completo no cambia de firma."""
        exp = tmp_path / "export"
        exp.mkdir()
        llamado = []

        def _export():
            llamado.append(True)
            return {"zip": str(exp / "cartucho-x.zip"), "ventas_filas": 1}

        h = _Harness().correr(exp, _export)
        assert llamado, "el export deberia haberse llamado"
        assert "Cartucho creado" in h.status.value

    def test_error_del_incremental_se_muestra(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()

        def _export(desde=None, hasta=None):
            raise ValueError("el rango 2026-02-01..9999-12-31 no tiene filas")

        h = _Harness().correr(exp, _export, desde="2026-02-01")
        assert "No se pudo crear el cartucho" in h.status.value
        assert "no tiene filas" in h.status.value
        assert h.btn.disabled is False, "el boton tiene que volver a habilitarse"


class TestMensajeIncremental:
    """Un incremental tiene que ANUNCIARSE: si el mensaje es el mismo que el
    del completo, el usuario cree que mando el historico entero y el cliente
    recibe una ventana y se queda sin el resto sin enterarse."""

    def test_el_incremental_dice_su_rango(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        r = {
            "zip": str(exp / "cartucho-x-inc-20260201.zip"),
            "ventas_filas": 12345,
            "zip_mb": 12,
            "incremental": True,
            "rango": {"desde": "2026-02-01", "hasta": "9999-12-31"},
        }
        texto, aviso = _msgs_cartucho(exp, r=r)
        assert "incremental" in texto.lower()
        assert "2026-02-01" in texto
        assert "no lo reemplaza" in texto, "el mensaje debe aclarar que suma, no pisa"
        assert "2026-02-01" in aviso

    def test_el_completo_no_se_presentationa_como_incremental(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        r = {
            "zip": str(exp / "cartucho-x.zip"),
            "ventas_filas": 10,
            "zip_mb": 800,
            "incremental": False,
        }
        texto, _ = _msgs_cartucho(exp, r=r)
        assert "incremental" not in texto.lower()
        assert "Copialo a la otra PC" in texto


class TestFechaDesdeLibre:
    """El campo acepta cualquier fecha (ej. 2026-08-01) y carga hasta hoy.

    Se valida con el dialogo abierto: si el formato esta mal, se dice cual
    se espera sin arrancar un export que va a fallar igual minutos despues.
    """

    def test_acepta_fecha_valida(self):
        from src.ui.view_panels import validar_fecha_desde

        assert validar_fecha_desde("2026-08-01") is None
        assert validar_fecha_desde(" 2026-08-01 ") is None

    def test_rechaza_vacia(self):
        from src.ui.view_panels import validar_fecha_desde

        assert "Pon la fecha" in (validar_fecha_desde("") or "")
        assert "Pon la fecha" in (validar_fecha_desde("   ") or "")

    def test_rechaza_formato_invalido(self):
        from src.ui.view_panels import validar_fecha_desde

        for mala in ("15/01/2026", "2026-8-1", "2026/08/01", "ayer", "20260801"):
            msg = validar_fecha_desde(mala)
            assert msg is not None and "AAAA-MM-DD" in msg, mala

    def test_rechaza_fecha_inexistente(self):
        from src.ui.view_panels import validar_fecha_desde

        for mala in ("2026-13-01", "2026-02-30", "2026-00-10"):
            msg = validar_fecha_desde(mala)
            assert msg is not None and "inexistente" in msg.lower(), mala


class TestAvisoParaCompartir:
    """Al terminar, hay que decir donde quedo el ZIP para llevarlo.

    El usuario pidio el cartucho para compartirlo a otra PC: un "exito"
    generico no dice que hacer. El aviso nombra el archivo.
    """

    def test_avisa_donde_compartir(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        zip_path = exp / "cartucho-x-inc.zip"
        zip_path.write_bytes(b"x" * 100)

        def _export(desde=None, hasta=None):
            return {
                "zip": str(zip_path),
                "ventas_filas": 10,
                "zip_mb": 1,
                "incremental": True,
                "rango": {"desde": "2026-08-01", "hasta": "9999-12-31"},
            }

        h = _Harness().correr(exp, _export, desde="2026-08-01")
        textos = " ".join(m for m, _ in h.snacks)
        assert "compartir" in textos.lower() or "copia" in textos.lower()
        assert "cartucho-x-inc.zip" in textos


class TestFlujoExport:
    """El flujo real (codigo de produccion), con exportar inyectado."""

    def test_exito_avisa_creando_y_creado(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()
        h = _Harness().correr(
            exp,
            lambda: {
                "zip": str(exp / "cartucho-20260930-101112.zip"),
                "ventas_filas": 2812871,
                "zip_mb": 655,
            },
        )
        # creando + creado + aviso para compartir el ZIP.
        assert len(h.snacks) == 3
        assert "Creando cartucho" in h.snacks[0][0]
        assert "Cartucho creado" in h.snacks[1][0]
        assert "cartucho-20260930-101112.zip" in h.snacks[2][0]
        assert h.status.color == "success"
        assert h.btn.disabled is False

    def test_error_rehabilita_el_boton(self, tmp_path):
        """El invariante: un fallo nunca debe dejar el boton deshabilitado."""
        exp = tmp_path / "export"
        exp.mkdir()

        def _boom():
            raise RuntimeError("integrity_check != ok, no se exporta")

        h = _Harness().correr(exp, _boom)
        assert h.btn.disabled is False, "boton muerto tras un error"
        assert h.status.color == "error"
        assert "integrity_check" in h.status.value
        assert h.snacks[-1][1] == "error"

    def test_nombre_duplicado_tiene_mensaje_propio(self, tmp_path):
        exp = tmp_path / "export"
        exp.mkdir()

        def _dup():
            raise FileExistsError(f"ya existe: {exp}/cartucho-20260930-101112")

        h = _Harness().correr(exp, _dup)
        assert "Ya existe un cartucho" in h.status.value
        assert h.btn.disabled is False

    def test_boton_se_bloquea_durante_y_se_libera(self, tmp_path):
        """Se deshabilita al arrancar (evita doble export) y se libera al final."""
        exp = tmp_path / "export"
        exp.mkdir()
        visto = {}

        def _lento():
            # dentro del export el boton ya debe estar bloqueado
            visto["durante"] = h.btn.disabled
            return {"zip": "z.zip", "ventas_filas": 1, "zip_mb": 1}

        h = _Harness()
        h.correr(exp, _lento)
        assert visto["durante"] is True
        assert h.btn.disabled is False

    def test_primera_alerta_ocurre_antes_de_exportar(self, tmp_path):
        """El aviso 'creando' tiene que salir antes de los minutos de compresion."""
        exp = tmp_path / "export"
        exp.mkdir()
        orden = []

        def _export():
            orden.append(("export", list(h.snacks)))
            return {"zip": "z.zip", "ventas_filas": 1, "zip_mb": 1}

        h = _Harness()
        # dispara el aviso de Creating de forma sincrona
        t = _correr_export_cartucho(
            carpeta_export=exp,
            status=h.status,
            btn_export=h.btn,
            app=h.app,
            page=h.page,
            exportar=_export,
        )
        assert any("Creando cartucho" in m for m, _ in h.snacks), "no aviso antes de arrancar"
        t.join(timeout=10)
        assert orden[0][0] == "export"
