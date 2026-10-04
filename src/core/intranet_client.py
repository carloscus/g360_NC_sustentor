"""Cliente HTTP intranet CIPSA — port de src/browser/http.rs (g360-ventas-db).

Flujo descubierto empiricamente en el repo Rust:
  1) GET del reporte fija el rango de fechas en la sesion del server y renderiza
     __VIEWSTATE fresco (reusar VIEWSTATE entre rangos devuelve datos obsoletos).
  2) POST del boton Exportar (accion=D0) devuelve el XLS de ESE rango. Algunas
     versiones del server responden HTML con content-type excel.
  3) Fallback: GET accion=L0 trae el grid ya renderizado; se extrae la tabla
     como CSV (via mas robusta y rapida, validada en produccion).

Transporte: httpx puro con cookie jar (equivale a reqwest + cookie_store).
"""

from __future__ import annotations
from src.core.fechas import fecha_ui

import csv
import html as html_mod
import html as _html_unescape_mod  # alias usado por extract_largest_table_html
import io
import logging
import re
import time
import warnings
from dataclasses import dataclass
from datetime import date

import httpx

log = logging.getLogger(__name__)

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

BASE = "http://intranet.cipsa.com.pe"
LOGIN_URL = f"{BASE}/intranetcipsa/login.aspx"
REPORT_PATH = "/ESTADISTICASVENTAS/Estadistica11.aspx"
BTN_EXPORT = "ctl00$ContentPlaceHolder1$btnExportar"
VALUE_DOCS = "01F%2c+01B%2c+01NCR%2c+01NDB"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class IntranetError(Exception):
    """Error de transporte/credenciales intranet."""


class SessionLost(IntranetError):
    """La sesion expiro — re-login y reintentar."""


@dataclass
class DownloadResult:
    kind: str  # "xls" | "html"
    payload: bytes
    content_type: str
    url: str
    t_get: float = 0.0  # seg: render del grid (fija el rango en sesion)
    t_post: float = 0.0  # seg: generacion y transferencia del export


def exp_url(df: str, dt: str, art_i: str = "", art_f: str = "", accion: str = "D0") -> str:
    return (
        f"{BASE}{REPORT_PATH}?valueCli=&valueVend=&valueVend2=&valueCli2="
        f"&valueSucI=&valueSucF=&accion={accion}&valueArtI={art_i}&valueArtF={art_f}"
        f"&valueAlmI=&valueAlmF=&valueFI={df}&valueFF={dt}&valueGrat=1&valueDocs={VALUE_DOCS}"
    )


def parse_hidden_fields(page: str) -> list[tuple[str, str]]:
    """Extrae hidden fields ASP.NET (__VIEWSTATE etc) igual que http.rs."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"<input\b", page, re.IGNORECASE):
        end = page.find(">", m.start())
        if end < 0:
            continue
        tag = page[m.start() : end]
        name_m = re.search(r'name="([^"]*)"', tag)
        if not name_m:
            continue
        name = name_m.group(1)
        if not name.startswith("__"):
            continue
        val_m = re.search(r'value="([^"]*)"', tag)
        value = val_m.group(1) if val_m else ""
        out.append((name, value))
    return out


def url_is_login(page: str) -> bool:
    return 'id="txtnombre"' in page or "id='txtnombre'" in page


def validate_export(payload: bytes, content_type: str) -> str:
    """Clasifica la respuesta del export. Devuelve 'xls' | 'html' o lanza error.
    Robusto a whitespace/BOM inicial: el magic byte puede venir despues de \\r\\n."""
    if len(payload) < 512:
        head = payload[:300].decode("utf-8", "ignore").lower()
        if "<html" in head or "error" in head or "exception" in head:
            raise IntranetError(
                f"export devolvio HTML de error ({len(payload)} bytes): {head[:150]}"
            )
    # Magic bytes sobre el payload SIN whitespace/BOM inicial
    body = payload.lstrip(b"\r\n \t\xef\xbb\xbf")
    is_ole = body[:4] == b"\xd0\xcf\x11\xe0"
    is_zip = body[:2] == b"PK"
    if is_ole or is_zip:
        return "xls"
    if body[:1] == b"<":
        # HTML: pagina de error, grid renderizado o export disfrazado
        return "html"
    if "text/html" in content_type:
        raise IntranetError(f"respuesta sospechosa text/html {len(payload)} bytes")
    return "xls"


class IntranetClient:
    """Sesion HTTP contra el intranet CIPSA (login ASP.NET WebForms)."""

    LOGIN_TIMEOUT = 60.0
    GET_TIMEOUT = 420.0  # render del grid puede tardar >200s en dias pesados
    POST_TIMEOUT = 420.0  # generacion del export para meses completos

    def __init__(
        self,
        user: str,
        password: str,
        timeout: float = 480.0,
        connect_timeout: float = 15.0,
    ):
        self.user = user
        self.password = password
        self._client = httpx.Client(
            verify=False,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )
        self._logged_in = False
        # Fase de la solicitud en vuelo (para heartbeats exactos en la UI)
        self.fase_actual = "inactiva"
        self.password = password
        self._client = httpx.Client(
            verify=False,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )
        self._logged_in = False

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Login ────────────────────────────────────────────────────────

    def login(self) -> None:
        t0 = time.time()
        resp = self._client.get(LOGIN_URL)
        resp.raise_for_status()
        page = resp.text
        form = dict(parse_hidden_fields(page))
        if not form:
            raise IntranetError("login.aspx sin hidden fields (server cambio?)")
        form["txtnombre"] = self.user
        form["txtpass"] = self.password
        form["Button1"] = "Aceptar"
        post = self._client.post(LOGIN_URL, data=form, timeout=60.0)
        final_url = str(post.url)
        if "login" in final_url:
            body = post.text
            if "txtnombre" in body and "txtpass" in body:
                raise IntranetError("Login HTTP fallo (credenciales rechazadas)")
        self._logged_in = True
        log.info("Login intranet OK en %.1fs", time.time() - t0)

    def ensure_logged_in(self) -> None:
        if not self._logged_in:
            self.login()

    # ── Verificación de credenciales + sesión ────────────────────────

    def verify_credentials(self) -> tuple[bool, str]:
        """Login real + sonda del reporte con un rango de 1 día.
        Devuelve (ok, mensaje) con info de sesión para mostrar en UI."""
        t0 = time.time()
        try:
            self.login()
        except IntranetError as e:
            return False, f"Credenciales rechazadas: {e}"
        except Exception as e:  # red/DNS/timeout
            return False, f"Sin acceso a la intranet: {e}"
        # Sonda: GET del reporte con rango de 1 día (no descarga nada pesado)
        ayer = date.today().toordinal() - 1
        d = fecha_ui(date.fromordinal(ayer))
        url = exp_url(d, d, "", "", "L0")
        try:
            resp = self._client.get(url, timeout=60.0)
            resp.raise_for_status()
            page = resp.text
            if url_is_login(page):
                self._logged_in = False
                return False, "Login aceptado pero la sesión no se mantiene"
            if len(page) < 5000:
                return False, (
                    f"Reporte no accesible (respuesta {len(page)} bytes) — "
                    "el usuario puede no tener permisos para ESTADISTICASVENTAS"
                )
            rows = extract_largest_table_html(page)
            n_rows = max(0, len(rows) - 1)
            dt_ms = (time.time() - t0) * 1000
            return True, (
                f"Credenciales OK — sesión activa en {dt_ms:.0f} ms · "
                f"reporte accesible ({n_rows} filas para {d})"
            )
        except Exception as e:
            return False, f"Login OK pero el reporte no respondió: {e}"

    # ── Descarga XLS (accion=D0, POST btnExportar) ───────────────────

    def get_and_post_export(self, url: str, timeout_post: float | None = None) -> DownloadResult:
        """GET fija el rango en la sesion; POST Exportar devuelve el archivo.
        Cada etapa se mide por separado (para diagnosticar donde se traba)."""
        t_post_lim = timeout_post or self.POST_TIMEOUT
        url_nocache = f"{url}&_t={int(time.time())}"
        t0 = time.time()
        self.fase_actual = "GET render del grid (server arma la vista del rango)"
        try:
            resp = self._client.get(
                url_nocache,
                timeout=self.GET_TIMEOUT,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise IntranetError(
                f"timeout en GET del grid tras {self.GET_TIMEOUT:.0f}s — server sin responder (rango muy pesado?)"
            ) from e
        t_get = time.time() - t0
        page = resp.text
        form = dict(parse_hidden_fields(page))
        if not form or url_is_login(page):
            raise SessionLost("sesion perdida (no hay __VIEWSTATE)")
        form[BTN_EXPORT] = "Exportar a "
        t1 = time.time()
        self.fase_actual = "POST generando XLS (server trabaja)"
        try:
            post = self._client.post(
                url_nocache,
                data=form,
                timeout=t_post_lim,
                headers={
                    "Referer": url_nocache,
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
            post.raise_for_status()
        except httpx.TimeoutException as e:
            raise IntranetError(
                f"timeout en POST export tras {t_post_lim:.0f}s — server no genera el archivo (usa split)"
            ) from e
        t_post = time.time() - t1
        content_type = post.headers.get("content-type", "")
        payload = post.content
        kind = validate_export(payload, content_type)
        log.info(
            "Downloaded %d bytes (%s) en GET %.1fs + POST %.1fs",
            len(payload),
            content_type,
            t_get,
            t_post,
        )
        return DownloadResult(
            kind=kind,
            payload=payload,
            content_type=content_type,
            url=url,
            t_get=t_get,
            t_post=t_post,
        )

    def download_export(
        self,
        desde: str,
        hasta: str,
        art_i: str = "",
        art_f: str = "",
        timeout_post: float | None = None,
    ) -> DownloadResult:
        """Export D0 con re-login automatico si la sesion se pierde."""
        url = exp_url(desde, hasta, art_i, art_f, "D0")
        try:
            return self.get_and_post_export(url, timeout_post=timeout_post)
        except SessionLost:
            log.warning("sesion perdida - re-login y reintento unico")
            self.login()
            return self.get_and_post_export(url, timeout_post=timeout_post)

    # ── Split por rango de articulo (port de http.rs download_day_split) ──

    # Esquemas 2/4/8 partes: rangos ASCII de ID_ARTICULO que cubren todo el abecedario
    # (el ERP ordena SKUs; "5ZZZZZ" corta despues de los 5xxxxx, ":" cubre 6-9, etc.)
    ART_SPLIT_SCHEMES: list[list[tuple[str, str]]] = [
        [("", "5ZZZZZ"), ("6", "ZZZZZ")],
        [("", "3ZZZZZ"), ("4", "6ZZZZZ"), ("7", "9ZZZZZ"), (":", "ZZZZZ")],
        [
            ("", "1ZZZZZ"),
            ("2", "3ZZZZZ"),
            ("4", "5ZZZZZ"),
            ("6", "7ZZZZZ"),
            ("8", "9ZZZZZ"),
            (":", "GZZZZZ"),
            ("H", "NZZZZZ"),
            ("O", "ZZZZZ"),
        ],
    ]

    def download_export_split(
        self,
        desde: str,
        hasta: str,
        schemes: list[list[tuple[str, str]]] | None = None,
    ) -> list[DownloadResult]:
        """Descarga en partes por rango de articulo (2 -> 4 -> 8 partes).
        Se usa cuando el export completo excede el tiempo del server.
        Devuelve las partes del primer esquema completo, o [] si todos fallan."""
        last_err: Exception | None = None
        for si, buckets in enumerate((schemes or self.ART_SPLIT_SCHEMES), 1):
            parts: list[DownloadResult] = []
            ok_all = True
            for bi, (art_i, art_f) in enumerate(buckets, 1):
                try:
                    res = self.download_export(desde, hasta, art_i, art_f)
                    if len(res.payload) < 500:
                        log.warning(
                            "split s%d/%d bucket %d sospechoso (%d bytes)",
                            si,
                            len(buckets),
                            bi,
                            len(res.payload),
                        )
                        ok_all = False
                        break
                    parts.append(res)
                except Exception as e:
                    last_err = e
                    log.warning("split s%d/%d bucket %d fallo: %s", si, len(buckets), bi, e)
                    ok_all = False
                    break
            if ok_all and parts:
                log.info("split articulos OK: esquema %d (%d partes)", si, len(buckets))
                return parts
        if last_err:
            raise IntranetError(f"todos los esquemas de split fallaron: {last_err}")
        return []

    # ── Scraping grid HTML (accion=L0) -> CSV ────────────────────────

    def scrape_html(self, desde: str, hasta: str) -> str:
        """GET accion=L0: grid DevExpress renderizado. Devuelve CSV."""
        url = exp_url(desde, hasta, accion="L0")
        resp = self._client.get(url, timeout=300.0)
        resp.raise_for_status()
        page = resp.text
        if len(page) < 5000 or url_is_login(page):
            raise IntranetError(f"L0 sin grid ({len(page)} bytes)")
        csv_text = scrape_grid(page)
        n_filas = csv_text.count("\n") if csv_text else 0
        if n_filas < 50:
            raise IntranetError(f"L0 tabla vacia ({n_filas} filas)")
        return csv_text


def extract_grid_devexpress(html: str) -> list[list[str]]:
    """Extrae el grid DevExpress (ASPxGridView) del reporte Estadistica11.

    - Headers: celdas class='dxgvHeader*' (texto en <td> interno anidado).
    - Filas: <tr id='...DXDataRowN'> con <td class='dxgv'> planos (44 cols).
    Devuelve [headers, *datos] — mismo contrato que extract_largest_table_html.
    [] si el HTML no es un grid DevExpress (el llamador usa fallback)."""
    headers: list[str] = []
    for frag in html.split('class="dxgvHeader'):
        if len(headers) >= 60:
            break
        m = re.search(r"<td[^>]*>([^<]{1,80})</td>", frag[:800], re.S)
        if m:
            name = html_mod.unescape(m.group(1)).strip()
            if name:
                headers.append(name)
    filas: list[list[str]] = []
    for m in re.finditer(r'<tr id="[^"]*DXDataRow\d+"[^>]*>(.*?)</tr>', html, re.S | re.I):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S | re.I)
        celdas = [" ".join(html_mod.unescape(re.sub(r"<[^>]+>", "", td)).split()) for td in tds]
        filas.append(celdas)
    if not headers or not filas:
        return []
    return [headers] + filas


def scrape_grid(html: str) -> str:
    """Grid L0 -> CSV. Prefiere el extractor DevExpress; fallback a tabla generica."""
    rows = extract_grid_devexpress(html)
    if len(rows) < 2:
        rows = extract_largest_table_html(html)
    if len(rows) < 2:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue()


def extract_largest_table_html(page: str) -> list[list[str]]:
    """Port de extract_largest_table_html (http.rs): tabla con mas filas -> matriz."""
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tables: list[list[list[str]]] = []
            self._table: list[list[str]] | None = None
            self._row: list[str] | None = None
            self._cell: list[str] | None = None

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self._table = []
            elif tag == "tr" and self._table is not None:
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []

        def handle_endtag(self, tag):
            if tag == "table" and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self._table.append(self._row)
                self._row = None
            elif tag in ("td", "th") and self._cell is not None:
                text = _html_unescape_mod.unescape("".join(self._cell))
                text = " ".join(text.split()).replace(",", " ")
                self._row.append(text)
                self._cell = None

        def handle_data(self, data):
            if self._cell is not None:
                self._cell.append(data)

    parser = TableParser()
    parser.feed(page)
    best: list[list[str]] = []
    for table in parser.tables:
        if len(table) > len(best):
            best = table
    return best


# ── Rangos (port de MonthRange, captor.rs) ──────────────────────────────────


@dataclass
class MonthRange:
    start: date
    end: date
    label: str

    def to_url_params(self) -> tuple[str, str]:
        return (fecha_ui(self.start), fecha_ui(self.end))


def month_chunk(y: int, m: int) -> MonthRange:
    start = date(y, m, 1)
    ey, em = (y + 1, 1) if m == 12 else (y, m + 1)
    end = date(ey, em, 1).toordinal() - 1
    return MonthRange(start=start, end=date.fromordinal(end), label=f"{y}-{m:02d}")


def day_chunk(d: date) -> MonthRange:
    return MonthRange(start=d, end=d, label=d.strftime("%Y-%m-%d"))


def month_chunks(desde: date, hasta: date) -> list[MonthRange]:
    """Chunks mensuales que cubren [desde, hasta]."""
    chunks: list[MonthRange] = []
    cur = date(desde.year, desde.month, 1)
    while cur <= hasta:
        mc = month_chunk(cur.year, cur.month)
        if mc.end >= desde and mc.start <= hasta:
            mc.start = max(mc.start, desde)
            mc.end = min(mc.end, hasta)
            mc.label = f"{cur.year}-{cur.month:02d}"
            chunks.append(mc)
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return chunks


def day_chunks(desde: date, hasta: date) -> list[MonthRange]:
    """Chunks diarios que cubren [desde, hasta]."""
    out: list[MonthRange] = []
    cur = desde
    while cur <= hasta:
        out.append(day_chunk(cur))
        cur = date.fromordinal(cur.toordinal() + 1)
    return out
