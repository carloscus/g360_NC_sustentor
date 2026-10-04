"""Sincronizacion incremental ERP <-> g360-ventas-api.

Flujo:
  1. status()/day_checksums() -> frescura del servidor.
  2. day_checksums vs agregado local -> dias a los que les falta data.
  3. ventas_por_dia() de cada dia desfasado (o diff de folios como fallback).
  4. replace_folios() -> escritura quirurgica (solo los folios del lote).

El paso 4 es idempotente: reemplaza folio por folio, asi que un diff parcial
nunca borra las lineas de los folios que no vinieron en la respuesta.

El checksum de dia de la API es un MD5 de 4 palabras que no se puede reproducir
local, asi que la comparacion es por ``(filas, soles)`` del dia: es la misma
metrica que ya usa el chequeo de superset del sync por archivo.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from .ventas_api_client import VentaAPIClient, VentaAPIError

log = logging.getLogger(__name__)


class SyncAPI:
    """Orquesta el diff de folios contra la API Go."""

    def __init__(self, api_client: VentaAPIClient):
        self.api_client = api_client

    # ── Frescura ─────────────────────────────────────────────────────

    def frescura(self, desde: str = "", hasta: str = "") -> Dict[str, Any]:
        """status() + conteo de dias con checksum.

        No consulta ``/api/checksums``: ese endpoint agregado agota el timeout
        del servidor (~10s) sobre la base completa. ``/api/day-checksums`` y
        ``/api/status`` responden bien y alcanzan para el proposito.
        """
        status = self.api_client.status()
        out: Dict[str, Any] = {
            "filas": status.get("filas"),
            "meses": status.get("meses"),
            "fecha_desde": status.get("fecha_desde"),
            "fecha_hasta": status.get("fecha_hasta"),
            "capturado_en_ultimo": status.get("capturado_en_ultimo"),
            "n_dias": 0,
            "status": status,
        }
        if desde and hasta:
            try:
                dias = self.api_client.day_checksums(desde, hasta).get("dias", [])
            except VentaAPIError as e:
                log.warning("day_checksums no disponible (%s, %s): %s", desde, hasta, e)
                dias = []
            out["n_dias"] = len(dias) if isinstance(dias, list) else 0
        return out

    # ── Diff de folios ───────────────────────────────────────────────

    @staticmethod
    def diff_folios(folios_api: Iterable[str], folios_locales: Iterable[str]) -> List[str]:
        """Folios del servidor que faltan en local (ordenados, sin duplicados)."""
        local: Set[str] = set(folios_locales)
        return sorted({f for f in folios_api if f and f not in local})

    def folios_faltantes(
        self,
        desde: str,
        hasta: str,
        folios_locales: Iterable[str],
    ) -> List[str]:
        """Folios del rango que el servidor tiene y local no."""
        remotos = self.api_client.folios(desde, hasta)
        faltantes = self.diff_folios(remotos, folios_locales)
        log.info(
            "Folios remotos=%d faltantes=%d [%s, %s]", len(remotos), len(faltantes), desde, hasta
        )
        return faltantes

    def fetch_faltantes(
        self,
        folios: List[str],
        desde: str = "",
        hasta: str = "",
    ) -> List[Dict[str, Any]]:
        """Filas completas (sin `id`) de los folios listados."""
        if not folios:
            return []
        return self.api_client.ventas_by_folios(folios, desde, hasta)

    # ── Lectura paginada ─────────────────────────────────────────────

    def fetch_objeto_todo(
        self,
        obj: str,
        page: int = 1000,
        max_filas: int = 0,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Descarga paginada de /api/data/{obj} hasta agotar o max_filas."""
        out: List[Dict[str, Any]] = []
        offset = 0
        while True:
            data = self.api_client.data(obj, limit=page, offset=offset, **kwargs)
            filas = data.get("datos", [])
            out.extend(filas)
            if len(filas) < page:
                break
            offset += page
            if max_filas and len(out) >= max_filas:
                return out[:max_filas]
        return out

    # ── Deteccion de cambios ─────────────────────────────────────────

    @staticmethod
    def dias_desfasados(
        desde: str,
        hasta: str,
        dias_api: Iterable[Dict[str, Any]],
        conn: sqlite3.Connection,
    ) -> List[str]:
        """Dias del rango a los que local les falta data.

        Solo se devuelven los dias a los que les **falta** data. Cuando local va
        adelantado (tiene mas filas/soles que el servidor, tipico si el snapshot
        del API es mas viejo que la ultima captura) el dia se reporta aparte
        (``dias_local_adelantado``) y no se toca: perseguir al servidor atrasado
        seria reescribir filas buenas una y otra vez.

        La consulta local usa el rango directo sobre ``fecha_orig`` para que pegue
        el indice ``idx_venta_fecha`` en vez de recorrer la tabla con ``substr``.
        """
        falta, _adelantado = SyncAPI._clasifica_dias(desde, hasta, dias_api, conn)
        return falta

    @classmethod
    def _clasifica_dias(
        cls,
        desde: str,
        hasta: str,
        dias_api: Iterable[Dict[str, Any]],
        conn: sqlite3.Connection,
    ) -> tuple[List[str], List[str]]:
        """``(dias_falta, dias_local_adelantado)`` en una sola pasada por cada lado."""
        remoto = cls._agregado_remoto(dias_api)
        local = cls._agregado_local(conn, desde, hasta)
        falta: List[str] = []
        adelantado: List[str] = []
        for dia, agg in remoto.items():
            estado = cls._clasifica(agg, local.get(dia))
            if estado == "falta":
                falta.append(dia)
            elif estado == "local_adelantado":
                adelantado.append(dia)
        return sorted(falta), sorted(adelantado)

    @staticmethod
    def _agregado_remoto(dias_api: Iterable[Dict[str, Any]]) -> Dict[str, tuple[int, float]]:
        """``{dia: (filas, soles)}`` segun ``/api/day-checksums``."""
        remoto: Dict[str, tuple[int, float]] = {}
        for d in dias_api or []:
            dia = str(d.get("dia") or "")[:10]
            if dia:
                remoto[dia] = (int(d.get("filas") or 0), float(d.get("soles") or 0))
        return remoto

    @staticmethod
    def _agregado_local(
        conn: sqlite3.Connection,
        desde: str,
        hasta: str,
    ) -> Dict[str, tuple[int, float]]:
        """``{dia: (filas, soles)}`` desde la tabla local, en una sola pasada."""
        local: Dict[str, tuple[int, float]] = {}
        for dia, n, tot in conn.execute(
            "SELECT substr(fecha_orig,1,10), COUNT(*), ROUND(COALESCE(SUM(soles),0),2) "
            "FROM ventas WHERE fecha_orig >= ? AND fecha_orig < date(?, '+1 day') "
            "GROUP BY substr(fecha_orig,1,10)",
            (desde, hasta),
        ):
            local[str(dia)] = (int(n), float(tot))
        return local

    @staticmethod
    def _clasifica(
        remoto: Optional[tuple[int, float]],
        local: Optional[tuple[int, float]],
    ) -> str:
        """``"falta"`` / ``"ok"`` / ``"local_adelantado"`` para un dia.

        Tolerancia de 1 centavo: los soles vienen redondeados por ambos lados.
        """
        if local is None:
            return "falta"
        if local[0] == remoto[0] and abs(local[1] - remoto[1]) <= 0.01:
            return "ok"
        if local[0] < remoto[0] or local[1] < remoto[1] - 0.01:
            return "falta"
        return "local_adelantado"

    @staticmethod
    def _mismo_agregado(
        remoto: Optional[tuple[int, float]],
        local: Optional[tuple[int, float]],
    ) -> bool:
        return SyncAPI._clasifica(remoto, local) == "ok"

    def _fetch_dias(
        self,
        dias: List[str],
        page: int = 1000,
        max_filas_por_dia: int = 0,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Filas completas (sin `id`) de cada dia, paginadas y sin duplicados."""
        from . import ventas_db

        cols = [c.strip() for c in ventas_db.INSERT_COLS.split(",")]
        out: Dict[str, List[Dict[str, Any]]] = {}
        for dia in dias:
            filas: List[Dict[str, Any]] = []
            offset = 0
            while True:
                lote = self.api_client.ventas_por_dia(dia, limit=page, offset=offset)
                filas.extend(lote)
                if len(lote) < page:
                    break
                offset += page
                if max_filas_por_dia and len(filas) >= max_filas_por_dia:
                    filas = filas[:max_filas_por_dia]
                    break
            vistos: Set[tuple] = set()
            unicos: List[Dict[str, Any]] = []
            for v in filas:
                k = tuple(str(v.get(c, "")) for c in cols)
                if k in vistos:
                    continue
                vistos.add(k)
                unicos.append(v)
            out[dia] = unicos
        return out

    # ── Escritura local ──────────────────────────────────────────────

    def aplicar(
        self,
        desde: str,
        hasta: str,
        conn: sqlite3.Connection | None = None,
        solo_faltantes: bool = True,
        usar_dias: bool = True,
    ) -> Dict[str, Any]:
        """Sync incremental del rango contra la API y escritura en la BD local.

        Con ``solo_faltantes=True`` (default) el cambio se detecta por dia:
        compara ``(filas, soles)`` de ``/api/day-checksums`` contra el agregado
        local y re-fetcha solo los dias a los que les falta data. Asi se detectan
        tambien los folios ya presentes que fueron recapturados, que un diff por
        folio no veria. Si ``/api/day-checksums`` no responde, cae al diff de
        folios.

        ``solo_faltantes=False`` refetcha todo el rango por folio (repairing).

        Reemplaza folio por folio (``ventas_db.replace_folios``): los folios que
        ya estan y no cambian no se tocan, asi que un diff parcial nunca borra
        las lineas de los folios vecinos. Es idempotente, se puede re-correr.

        Nunca borra un folio que el API no menciono, y nunca toca un dia donde
        local ya va adelantado: la data local siempre gana ante un servidor atrasado.
        Los dias con mas data local que remota se listan en
        ``dias_local_adelantado`` sin escribir nada.

        Returns: {estado, modo, desde, hasta, folios_api, folios_local,
        folios_faltantes, filas, dias, dias_desfasados, dias_local_adelantado,
        dias_converjados, meses, segundos}.
        """
        from . import ventas_db

        t0 = time.time()
        api_folios = self.api_client.folios(desde, hasta)
        propio = conn is None
        conn = ventas_db.connect() if conn is None else conn
        if propio:
            # En PC nueva la DB puede no tener schema: crearlo es idempotente
            # y evita "no such table: ventas" en el primer sync.
            ventas_db.init_db()
        try:
            locales = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT folio_unico FROM ventas "
                    "WHERE substr(fecha_orig, 1, 10) >= ? AND substr(fecha_orig, 1, 10) <= ?",
                    (desde, hasta),
                )
                if r[0]
            }
            faltantes = self.diff_folios(api_folios, locales)
            base: Dict[str, Any] = {
                "desde": desde,
                "hasta": hasta,
                "folios_api": len(api_folios),
                "folios_local": len(locales),
                "folios_faltantes": len(faltantes),
            }

            desfasados: List[str] = []
            adelantado: List[str] = []
            remoto: Dict[str, tuple[int, float]] = {}
            modo = "rango" if not solo_faltantes else "folios"
            if solo_faltantes and usar_dias:
                try:
                    dias_api = self.api_client.day_checksums(desde, hasta).get("dias") or []
                    remoto = self._agregado_remoto(dias_api)
                    desfasados, adelantado = self._clasifica_dias(desde, hasta, dias_api, conn)
                    modo = "dias"
                except VentaAPIError as e:
                    log.warning(
                        "day-checksums no disponible (%s, %s): %s; diff de folios", desde, hasta, e
                    )
            if adelantado:
                log.info(
                    "sync_api: %d dias con mas data local que remoto (API atrasado): %s; "
                    "no se tocan",
                    len(adelantado),
                    ", ".join(adelantado),
                )
            if modo == "dias" and not desfasados:
                return {
                    **base,
                    "filas": 0,
                    "dias": [],
                    "dias_desfasados": [],
                    "dias_local_adelantado": adelantado,
                    "dias_converjados": 0,
                    "dias_sin_converger": 0,
                    "meses": [],
                    "modo": modo,
                    "segundos": round(time.time() - t0, 1),
                    "estado": "sin_cambios",
                }

            if modo == "dias":
                por_dia = self._fetch_dias(desfasados)
            else:
                a_procesar = sorted({f for f in api_folios if f}) if modo == "rango" else faltantes
                if not a_procesar:
                    return {
                        **base,
                        "filas": 0,
                        "dias": [],
                        "dias_desfasados": [],
                        "meses": [],
                        "modo": modo,
                        "segundos": round(time.time() - t0, 1),
                        "estado": "sin_cambios",
                    }
                filas = self.fetch_faltantes(a_procesar, desde, hasta)
                por_dia = {}
                for v in filas:
                    dia = str(v.get("fecha_orig", ""))[:10]
                    if dia:
                        por_dia.setdefault(dia, []).append(v)
                log.info(
                    "sync_api[%s]: %d folios -> %d filas en %d dias",
                    modo,
                    len(a_procesar),
                    len(filas),
                    len(por_dia),
                )

            # Sin folio_unico no hay reemplazo idempotente: insertar esas filas
            # produciria duplicados en cada corrida, asi que se descartan.
            escritas: Dict[str, List[Dict[str, Any]]] = {}
            recibidas = 0
            for dia, filas_dia in por_dia.items():
                recibidas += len(filas_dia)
                con_folio = [v for v in filas_dia if str(v.get("folio_unico") or "").strip()]
                if con_folio:
                    escritas[dia] = con_folio
            descartadas = recibidas - sum(len(v) for v in escritas.values())
            if descartadas:
                log.warning("API devolvio %d filas sin folio_unico; se descartan", descartadas)

            for dia in sorted(escritas):
                ventas_db.replace_folios(conn, escritas[dia])
                ventas_db.record_day_checksum(conn, dia)
            con_folio = [v for dia in sorted(escritas) for v in escritas[dia]]
            meses = sorted({str(v.get("mes_ref", ""))[:7] for v in con_folio} - {""})
            for mes in meses:
                ventas_db.record_month_checksum(conn, mes)

            # Un dia puede no converger: el API no menciona folios que local ya
            # tiene (p.ej. el productor todavia no los subio). No se borran --
            # solo se reemplazan los folios que el API si devolvio -- asi que el
            # dia queda desfasado y se vuelve a revisar en la proxima corrida.
            convergen = 0
            if modo == "dias" and desfasados:
                local = self._agregado_local(conn, desfasados[0], desfasados[-1])
                convergen = sum(
                    1 for d in desfasados if self._mismo_agregado(remoto.get(d), local.get(d))
                )
                if len(desfasados) - convergen:
                    log.info(
                        "sync_api: %d/%d dias convergen; el resto sigue con diferencias",
                        convergen,
                        len(desfasados),
                    )

            dur = time.time() - t0
            ventas_db.register_sync(conn, "sync_api", "ok", len(con_folio), dur)
            log.info(
                "sync_api: %d filas en %d dias (%.1fs) modo=%s",
                len(con_folio),
                len(escritas),
                dur,
                modo,
            )
            return {
                **base,
                "filas": len(con_folio),
                "dias": sorted(escritas),
                "dias_desfasados": desfasados,
                "dias_local_adelantado": adelantado,
                "dias_converjados": convergen,
                "dias_sin_converger": len(desfasados) - convergen,
                "meses": meses,
                "modo": modo,
                "descartadas_sin_folio": descartadas,
                "segundos": round(dur, 1),
                "estado": "ok",
            }
        except Exception as e:
            try:
                ventas_db.register_sync(
                    conn, "sync_api", "error", 0, time.time() - t0, str(e)[:500]
                )
            except Exception:
                pass
            raise
        finally:
            if propio:
                conn.close()

    def close(self) -> None:
        self.api_client.close()
