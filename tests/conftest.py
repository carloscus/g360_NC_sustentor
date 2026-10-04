"""Fixtures para tests de la capa de datos local (SQLite + captura)."""

import os

import pytest


@pytest.fixture(scope="session")
def _data_dir_aislado(tmp_path_factory):
    return tmp_path_factory.mktemp("g360_data")


@pytest.fixture(autouse=True)
def _aislar_db_real(_data_dir_aislado, request):
    """Red de seguridad: ningun test debe tocar la DB real de produccion.

    `data_dir()` cae a %APPDATA%\\g360-db-ventas\\data cuando G360_DATA_DIR no esta
    seteado, asi que un test que olvide `tmp_db` abre la base de 2.5 GB. Se vio
    justo en `test_coverage_summary`, que leia 2,815,148 filas de produccion y
    fallaba por datos ajenos. Los tests que necesitan su propia DB siguen
    sobrescribiendo G360_DATA_DIR con monkeypatch.

    Los guards que SI auditan la base real se marcan con `@pytest.mark.real_db`
    y quedan fuera del aislamiento.
    """
    if request.node.get_closest_marker("real_db"):
        yield
        return
    previo = os.environ.get("G360_DATA_DIR")
    os.environ["G360_DATA_DIR"] = str(_data_dir_aislado)
    yield
    if previo is None:
        os.environ.pop("G360_DATA_DIR", None)
    else:
        os.environ["G360_DATA_DIR"] = previo


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """DB SQLite temporal aislada por test (via G360_DATA_DIR)."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
    os.environ.pop("G360_INTRANET_USER", None)
    os.environ.pop("G360_INTRANET_PASS", None)

    from src.core import ventas_db

    _reset_caches(ventas_db)
    ventas_db.init_db()
    ventas_db.init_estado()
    yield ventas_db
    _reset_caches(ventas_db)


def _reset_caches(ventas_db) -> None:
    """Limpia TODOS los caches de modulo de ventas_db.

    Faltaba `invalidate_card_info_cache`: sin el, `coverage_summary()` y las
    cards devolvian el `card_info` cacheado de otro test (o de la DB real).
    """
    ventas_db.reset_allowed_lines_cache()
    ventas_db.invalidate_lineas_cache()
    ventas_db.invalidate_sucursales_cache()
    ventas_db.invalidate_card_info_cache()


@pytest.fixture
def sample_ventas():
    """Conjunto de ventas de muestra: factura 2 lineas, NCR devolucion,
    NCR ajuste valor y NDB (incremento valor) cross-month."""
    base = dict(
        id_articulo="02211",
        original_sku="02211",
        nom_articulo="GASEOSA 3L",
        id_linea="0101",
        nom_linea="GASEOSAS",
        id_grupo="01",
        nom_grupo="G",
        id_tipo="01",
        nom_tipo="T",
        id_familia="01",
        nom_familia="F",
        id_cliente="00068414",
        doc_cliente="20100047218",
        nom_cliente="CLIENTE DEMO SAC",
        tpo_doc="F012",
        serie_doc="012",
        nro_doc="457996",
        referencia="",
        moneda="Soles",
        cantidad=100.0,
        cantidad_fae=0.0,
        soles=250.0,
        dolares=0.0,
        precio_unitario=2.5,
        anho=2024,
        mes=1,
        fecha_orig="2024-01-15",
        fecha_ref=None,
        fecha_venc=None,
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="SAN ISIDRO",
        id_vendedor="01177",
        nom_vendedor="VENDEDOR UNO",
        id_pedido="P1",
        file_source="test",
        mes_ref="2024-01",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="",
    )

    from src.core.xls_processor import derivar_campos

    ncr1 = dict(
        base,
        tpo_doc="NCR",
        serie_doc="N012",
        nro_doc="900001",
        referencia="F01/012-457996",
        cantidad=-10.0,
        soles=-25.0,
        precio_unitario=2.5,
        fecha_orig="2024-02-10",
        mes_ref="2024-02",
        folio_unico="",
    )
    ajuste = dict(
        base,
        tpo_doc="NCR",
        serie_doc="N012",
        nro_doc="900002",
        referencia="F01/012-457996",
        cantidad=0.0,
        cantidad_fae=100.0,
        soles=-50.0,
        precio_unitario=0.5,
        fecha_orig="2024-02-15",
        mes_ref="2024-02",
        folio_unico="",
    )
    ndb = dict(
        base,
        tpo_doc="NDB",
        serie_doc="N012",
        nro_doc="900003",
        referencia="F01/012-457996",
        cantidad=0.0,
        cantidad_fae=10.0,
        soles=30.0,
        precio_unitario=0.0,
        fecha_orig="2024-02-20",
        mes_ref="2024-02",
        folio_unico="",
    )
    rows = [
        dict(base),
        dict(
            base,
            id_articulo="03315",
            nom_articulo="JUGO 1L",
            cantidad=50.0,
            soles=100.0,
            precio_unitario=2.0,
        ),
        ncr1,
        ajuste,
        ndb,
    ]
    for v in rows:
        derivar_campos(v)
    return rows


@pytest.fixture
def populated_db(tmp_db, sample_ventas):
    """DB con las ventas de muestra ya insertadas."""
    from src.core import ventas_db

    conn = ventas_db.get_conn()
    facturas = [v for v in sample_ventas if v["mes_ref"] == "2024-01"]
    ncs = [v for v in sample_ventas if v["mes_ref"] == "2024-02"]
    ventas_db.insert_ventas(conn, facturas)
    ventas_db.insert_ventas(conn, ncs)
    ventas_db.populate_nc_asociadas(conn)
    return ventas_db
