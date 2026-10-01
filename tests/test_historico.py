"""Histórico mensual, conciliación y toggle NC/ND (§22: 7-13)."""

from openpyxl import load_workbook

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient
from src.ui import reporte_compras as rc


def _base(**kw):
    d = dict(
        id_articulo="014850",
        original_sku="014850",
        nom_articulo="PELOTA PVC #5",
        id_linea="0101",
        nom_linea="GASEOSAS",
        id_grupo="01",
        nom_grupo="G",
        id_tipo="01",
        nom_tipo="T",
        id_familia="01",
        nom_familia="F",
        id_cliente="00056101",
        doc_cliente="20601024714",
        nom_cliente="MULTICOPIAS MARY E.I.R.L.",
        tpo_doc="F01",
        serie_doc="204",
        nro_doc="67375",
        referencia="",
        moneda="Soles",
        cantidad=100.0,
        cantidad_fae=100.0,
        soles=1000.0,
        dolares=0.0,
        precio_unitario=10.0,
        anho=2026,
        mes=6,
        fecha_orig="2026-06-05",
        fecha_ref=None,
        fecha_venc=None,
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="SAN ISIDRO",
        id_vendedor="01188",
        nom_vendedor="VEND",
        id_pedido="P1",
        file_source="t",
        mes_ref="2026-06",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="",
    )
    d.update(kw)
    return d


def _poblar_hist(tmp_db):
    from src.core.xls_processor import derivar_campos

    rows = [
        _base(
            nro_doc="60001",
            cantidad=50.0,
            soles=500.0,
            anho=2024,
            mes=6,
            fecha_orig="2024-06-05",
            mes_ref="2024-06",
        ),
        _base(
            nro_doc="61001",
            cantidad=100.0,
            soles=1000.0,
            anho=2025,
            mes=8,
            fecha_orig="2025-08-10",
            mes_ref="2025-08",
        ),
        _base(),
        _base(
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=-20.0,
            cantidad_fae=-20.0,
            soles=-200.0,
            fecha_orig="2026-06-20",
            folio_unico="",
        ),
        _base(
            nro_doc="67380",
            cantidad=300.0,
            soles=3000.0,
            anho=2026,
            mes=8,
            fecha_orig="2026-08-05",
            mes_ref="2026-08",
        ),
        _base(
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900011",
            referencia="F01/204-67380",
            cantidad=0.0,
            cantidad_fae=-5.0,
            soles=-50.0,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-10",
            mes_ref="2026-09",
            folio_unico="",
        ),
    ]
    out = []
    for v in rows:
        derivar_campos(v)
        out.append(v)
    conn = tmp_db.get_conn()
    ventas_db.insert_ventas(conn, out)
    return out


def _hist(tmp_db, **kw):
    _poblar_hist(tmp_db)
    return VentasDbClient().fetch_historico_mensual(
        "00056101", fecha_desde="2024-01-01", fecha_hasta="2026-09-25", **kw
    )


class TestFetchHistorico:
    def test_filas_y_split(self, tmp_db):
        df = _hist(tmp_db)
        assert df["MES"].tolist() == ["2024-06", "2025-08", "2026-06", "2026-08", "2026-09"]
        j = df[df["MES"] == "2026-06"].iloc[0]
        assert (j["BRUTA"], j["DEV_S"], j["DESC_S"], j["NDB_S"], j["NETA"]) == (
            1000.0,
            -200.0,
            0.0,
            0.0,
            800.0,
        )
        assert (j["UFACT"], j["UDEV"]) == (100.0, -20.0)
        assert (j["FACTURAS"], j["NC"]) == (1, 1)
        s = df[df["MES"] == "2026-09"].iloc[0]
        assert (s["BRUTA"], s["DESC_S"], s["NETA"]) == (0.0, -50.0, -50.0)
        assert (s["UFACT"], s["FACTURAS"]) == (0.0, 0)

    def test_reconcilia(self, tmp_db):
        assert rc.reconciliar_mensual(_hist(tmp_db)) == []

    def test_reconcilia_detecta(self, tmp_db):
        df = _hist(tmp_db)
        df.loc[df["MES"] == "2026-06", "NETA"] = 0.0
        alertas = rc.reconciliar_mensual(df)
        assert any("2026-06" in a for a in alertas)

    def test_diario(self, tmp_db):
        _poblar_hist(tmp_db)
        df = VentasDbClient().fetch_historico_diario(
            "00056101", fecha_desde="2026-06-01", fecha_hasta="2026-06-30"
        )
        assert set(df["FECHA"]) == {"2026-06-05", "2026-06-20"}
        assert round(float(df["BRUTA"].sum()), 2) == 1000.0

    def test_toggle_off_solo_bruta(self, tmp_db):
        df = _hist(tmp_db, incluir_nc=False)
        j = df[df["MES"] == "2026-06"].iloc[0]
        assert (j["BRUTA"], j["NETA"]) == (1000.0, 1000.0)
        assert (j["DEV_S"], j["DESC_S"], j["NDB_S"]) == (0.0, 0.0, 0.0)
        assert (j["NC"], j["NDB_DOCS"]) == (0, 0)
        assert (j["UFACT"], j["UDEV"]) == (100.0, 0.0)
        assert rc.reconciliar_mensual(df) == []
        dia = VentasDbClient().fetch_historico_diario(
            "00056101", fecha_desde="2026-06-01", fecha_hasta="2026-06-30", incluir_nc=False
        )
        assert round(float(dia["BRUTA"].sum()), 2) == 1000.0
        assert round(float(dia["DEV_S"].sum()), 2) == 0.0

    def test_descuento_embebido_en_factura(self, tmp_db):
        """F/B con soles<0 va al bucket DESC (bruta la excluye)."""
        from src.core.xls_processor import derivar_campos

        # insert_ventas es delete-then-insert por mes_ref: el embebido va
        # primero para no borrar la fila de septiembre del poblador.
        emb = _base(
            nro_doc="67390",
            cantidad=5.0,
            soles=-50.0,
            precio_unitario=-10.0,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-12",
            mes_ref="2026-09",
        )
        derivar_campos(emb)
        ventas_db.insert_ventas(tmp_db.get_conn(), [emb])
        _poblar_hist(tmp_db)
        df = VentasDbClient().fetch_historico_mensual(
            "00056101", fecha_desde="2026-09-01", fecha_hasta="2026-09-30"
        )
        s = df[df["MES"] == "2026-09"].iloc[0]
        assert (s["BRUTA"], s["DESC_S"], s["NETA"]) == (0.0, -100.0, -100.0)
        assert s["UFACT"] == 5.0  # físico sin cambios
        assert rc.reconciliar_mensual(df) == []


class TestResumenMensual:
    def _wb(self, tmp_db, tmp_path, incluir_nc=True):
        _poblar_hist(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2024-01-01", fecha_hasta="2026-09-25")
        df_hist = cli.fetch_historico_mensual("00056101", incluir_nc=incluir_nc, **kw)
        out = tmp_path / "H.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_hist=df_hist,
            corte_hasta="2026-09-25",
            incluir_nc=incluir_nc,
            hojas={"resumen"},
        )
        return load_workbook(str(out))

    def test_tabla_mensual_formulas_y_estado(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Resumen Ejecutivo"]
        hdr = next(r for r in range(1, 30) if ws.cell(row=r, column=1).value == "Mes")
        assert [ws.cell(row=hdr, column=c).value for c in range(1, 17)] == [
            "Mes",
            "Venta bruta",
            "Dev. S/",
            "Desc. S/",
            "NDB S/",
            "Venta neta",
            "U. fact.",
            "U. dev.",
            "U. netas",
            "% dev.",
            "Facturas",
            "NC",
            "NDB docs",
            "SKUs",
            "Ticket",
            "Estado",
        ]
        assert ws["A1"].value == "COMPRAS NETAS — RESUMEN EJECUTIVO"
        assert ws.cell(row=hdr + 1, column=6).value == (f"=SUM(B{hdr + 1}:E{hdr + 1})")
        assert ws.cell(row=hdr + 1, column=9).value == (f"=G{hdr + 1}+H{hdr + 1}")
        assert ws.cell(row=hdr + 1, column=10).value == (
            f'=IF(G{hdr + 1}=0,"—",-H{hdr + 1}/G{hdr + 1})'
        )
        assert ws.cell(row=hdr + 1, column=15).value == (
            f'=IF(K{hdr + 1}=0,"—",B{hdr + 1}/K{hdr + 1})'
        )
        # Fila TOTAL: 16 celdas exactas (sin desfasaje): sums B..O + "".
        rt = next(
            r for r in range(hdr + 1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "TOTAL"
        )
        assert ws.cell(row=rt, column=2).value.startswith("=SUBTOTAL(109,B")
        assert ws.cell(row=rt, column=15).value == (
            f'=IF(K{rt}=0,"—",B{rt}/K{rt})'
        )  # ratio ticket en O
        assert ws.cell(row=rt, column=16).value in ("", None)  # Estado
        assert ws.cell(row=rt, column=17).value is None  # sin celda fantasma
        estados = [ws.cell(row=r, column=16).value for r in range(hdr + 1, hdr + 6)]
        assert estados[:4] == ["COMPLETO"] * 4
        assert estados[4] == "PARCIAL"  # septiembre al 25, parcial
        textos = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
        assert any("Concilia" in t for t in textos)
        assert any("Incluir NC/ND: SÍ" in t for t in textos)

    def test_modo_toggle_en_nota(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path, incluir_nc=False)["Resumen Ejecutivo"]
        textos = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
        assert any("Incluir NC/ND: NO" in t for t in textos)


class TestFormulasEstructura:
    """Sin LibreOffice disponible no hay recálculo: se valida estructura.

    Funciones permitidas (era-2007, evaluables en Excel y LibreOffice),
    sin #REF! ni referencias externas; los valores los cubre la réplica
    Python (fetch/pivot) ya testeada.
    """

    _PERMITIDAS = {
        "SUM",
        "SUBTOTAL",
        "SUMIFS",
        "IF",
        "IFERROR",
        "INDEX",
        "MATCH",
        "ROUND",
        "TEXT",
        "MONTH",
        "DAY",
        "YEAR",
        "DATE",
        "EOMONTH",
        "MIN",
        "MAX",
        "AVERAGE",
        "ABS",
        "OR",
        "AND",
        "HYPERLINK",
        "ISNUMBER",
    }

    @staticmethod
    def _validar(wb, min_fx):
        import re

        n_fx = 0
        for ws in wb.worksheets:
            for r in ws.iter_rows():
                for c in r:
                    v = c.value
                    if not (isinstance(v, str) and v.startswith("=")):
                        continue
                    n_fx += 1
                    assert "#REF!" not in v and "[" not in v, (ws.title, v)
                    for fn in re.findall(r"([A-Z][A-Z0-9.]*)\(", v):
                        assert fn in TestFormulasEstructura._PERMITIDAS, (ws.title, v)
        assert n_fx >= min_fx

    def test_formulas_validas_en_paquete(self, tmp_db, tmp_path):
        _poblar_hist(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2024-01-01", fecha_hasta="2026-09-25")
        bundle = rc._paquete_export(cli, "00056101", kw["fecha_desde"], kw["fecha_hasta"], True)
        out = tmp_path / "F.xlsx"
        rc._escribir_xlsx(out, "56101", "M", corte_hasta="2026-09-25", **bundle)
        wb = load_workbook(str(out))
        assert wb.sheetnames == [
            "Resumen Ejecutivo",
            "Consolidado",
            "Comparativo",
            "Sucursales",
            "Sucursal_Linea_Mes",
            "Sucursal_SKU_Mes",
            "Ajustes_NC_NDB",
            "BD_Registro",
            "Facturas",
        ]
        self._validar(wb, 50)  # el paquete de 5 hojas va con fórmulas

    def test_formulas_con_hipervinculos(self, tmp_db, tmp_path):
        """Facturas con NC asociadas genera =HYPERLINK(...) validables."""
        _poblar_hist(tmp_db)  # NCR referencia a 204-67375
        conn = tmp_db.get_conn()
        tmp_db.populate_nc_asociadas(conn)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2024-01-01", fecha_hasta="2026-09-25")
        df_det = cli.fetch_facturas_sku_detalle_cliente("00056101", **kw)
        assert any(len(list(x)) for x in df_det["NC"])  # hay asociadas
        out = tmp_path / "H.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_dev=cli.fetch_notas_sku_cliente("00056101", **kw),
            df_huerf=cli.fetch_notas_huerfanas_cliente("00056101", **kw),
            df_det=df_det,
            hojas={"ajustes", "facturas"},
        )
        wb = load_workbook(str(out))
        link = [
            c.value
            for r in wb["Facturas"].iter_rows(min_col=15, max_col=15)
            for c in r
            if isinstance(c.value, str) and "HYPERLINK" in c.value
        ]
        assert link and "Ajustes_NC_NDB" in link[0]
        self._validar(wb, 5)
