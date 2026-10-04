"""Cliente Python para la API Go de ventas (g360-ventas-api).

Cubre las rutas reales del servidor (ver handleIndex en internal/handlers):
  POST /api/login, GET /api/health, /api/status, /api/checksums,
  /api/day-checksums, /api/folios, /api/contrast, POST /api/ventas/by-folios,
  /api/model, /api/data/{obj}, /api/query/{vista}, /api/stats,
  /api/export/list, /api/export/base-canonica.

El token se obtiene con login() (o con src.core.api_auth.APIAuthClient) y
viaja en `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from src.core.api_auth import default_api_url

log = logging.getLogger(__name__)


class VentaAPIError(Exception):
    """Error devuelto por la API Go (HTTP 4xx/5xx o red)."""

    def __init__(self, mensaje: str, status: int = 0):
        super().__init__(mensaje)
        self.status = status


class TokenInvalidoError(VentaAPIError):
    """HTTP 401/403: el token cached no sirve (expirado o del servidor anterior).

    Vale la pena una clase propia porque el unico reintento que tiene sentido
    es renovar el token y repetir una vez: ante un 401 no Reintentar sin
    cambiar las credenciales, solo se gana un error mas lento.
    """


class VentaAPIClient:
    """Cliente HTTP contra g360-ventas-api (solo lectura)."""

    def __init__(
        self,
        base_url: str = "",
        api_token: str = "",
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or default_api_url()).rstrip("/")
        self.api_token = api_token
        self.client = httpx.Client(timeout=timeout)
        self.user: str = ""

    def set_token(self, token: str, user: str = "") -> None:
        self.api_token = token
        if user:
            self.user = user

    def _headers(self, auth: bool = True) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if auth and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _raise(self, method: str, path: str, resp: httpx.Response) -> None:
        try:
            detail = resp.json().get("error", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        msg = f"{method} {path} -> HTTP {resp.status_code}: {detail}"
        if resp.status_code in (401, 403):
            raise TokenInvalidoError(msg, status=resp.status_code)
        raise VentaAPIError(msg, status=resp.status_code)

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Dict[str, Any]:
        try:
            resp = self.client.get(
                f"{self.base_url}{path}", headers=self._headers(auth), params=params
            )
        except httpx.HTTPError as e:
            raise VentaAPIError(f"GET {path}: {e}") from e
        if resp.status_code != 200:
            self._raise("GET", path, resp)
        return resp.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self.client.post(f"{self.base_url}{path}", headers=self._headers(), json=payload)
        except httpx.HTTPError as e:
            raise VentaAPIError(f"POST {path}: {e}") from e
        if resp.status_code != 200:
            self._raise("POST", path, resp)
        return resp.json()

    # ── Auth / salud (sin token salvo login) ──────────────────────────

    def login(self, user: str, password: str) -> Dict[str, Any]:
        """POST /api/login -> guarda el token y devuelve la respuesta."""
        data = self._post_auth(user, password)
        token = str(data.get("token", ""))
        if not token:
            raise VentaAPIError("POST /api/login: respuesta sin token")
        self.set_token(token, str(data.get("user", user)))
        return data

    def _post_auth(self, user: str, password: str) -> Dict[str, Any]:
        try:
            resp = self.client.post(
                f"{self.base_url}/api/login",
                headers={"Accept": "application/json"},
                json={"user": user, "password": password},
            )
        except httpx.HTTPError as e:
            raise VentaAPIError(f"POST /api/login: {e}") from e
        if resp.status_code != 200:
            self._raise("POST", "/api/login", resp)
        return resp.json()

    def health(self) -> Dict[str, Any]:
        """GET /api/health (sin auth)."""
        return self._get("/api/health", auth=False)

    # ── Sync incremental ──────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """GET /api/status: filas, cobertura fecha_orig, ultimo capturado_en."""
        return self._get("/api/status")

    def checksums(self) -> Dict[str, Any]:
        """GET /api/checksums: checksum por mes (cache 60s en server)."""
        return self._get("/api/checksums")

    def day_checksums(self, desde: str, hasta: str) -> Dict[str, Any]:
        """GET /api/day-checksums?desde=YYYY-MM-DD&hasta=... (max 1500 dias)."""
        return self._get("/api/day-checksums", {"desde": desde, "hasta": hasta})

    def folios(self, desde: str, hasta: str) -> List[str]:
        """GET /api/folios?desde&hasta -> DISTINCT tpo||serie||nro del rango."""
        data = self._get("/api/folios", {"desde": desde, "hasta": hasta})
        folios = data.get("folios", [])
        return [str(f) for f in folios]

    def contrast(self, desde: str, hasta: str) -> Dict[str, Any]:
        """GET /api/contrast?desde&hasta: filas/soles/cobertura del rango."""
        return self._get("/api/contrast", {"desde": desde, "hasta": hasta})

    def ventas_by_folios(
        self,
        folios: List[str],
        desde: str = "",
        hasta: str = "",
        chunk: int = 400,
    ) -> List[Dict[str, Any]]:
        """POST /api/ventas/by-folios (max 500 por request; pagina en chunks)."""
        out: List[Dict[str, Any]] = []
        for i in range(0, len(folios), chunk):
            payload: Dict[str, Any] = {"folios": folios[i : i + chunk]}
            if desde and hasta:
                payload["desde"] = desde
                payload["hasta"] = hasta
            data = self._post("/api/ventas/by-folios", payload)
            filas = data.get("filas") or data.get("datos") or []
            out.extend(filas)
        return out

    def ventas_por_dia(self, dia: str, limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
        """GET /api/data/ventas?fecha_orig=eq.{dia} -> filas del dia (paginado).

        `fecha_orig` es texto 'YYYY-MM-DD' en la API, asi que el filtro de
        igualdad es exacto. Es la via para re-fetchar un dia completo cuando su
        checksum cambio (folio recapturado), sin bajar el rango entero.
        """
        data = self.data("ventas", limit=limit, offset=offset, filtros={"fecha_orig": f"eq.{dia}"})
        return data.get("datos") or []

    # ── Modelo / datos ────────────────────────────────────────────────

    def model(self) -> Dict[str, Any]:
        """GET /api/model: tablas + vistas permitidas con columnas."""
        return self._get("/api/model")

    def data(
        self,
        obj: str,
        select: str = "",
        order: str = "",
        limit: int = 100,
        offset: int = 0,
        filtros: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """GET /api/data/{obj} con filtros estilo PostgREST.

        filtros: {"columna": "eq.valor", ...} p.ej. {"id_cliente": "eq.00068414"}.
        Devuelve {objeto, tipo, filas, limit, offset, datos}.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if select:
            params["select"] = select
        if order:
            params["order"] = order
        if filtros:
            params.update(filtros)
        return self._get(f"/api/data/{obj}", params)

    def query(self, vista: str, **kwargs: Any) -> Dict[str, Any]:
        """GET /api/query/{vista}: alias de data() para vistas."""
        return self.data(vista, **kwargs)

    def stats(self) -> Dict[str, Any]:
        """GET /api/stats: metricas globales + por mes (cache 60s)."""
        return self._get("/api/stats")

    # ── Base canonica (bootstrap) ─────────────────────────────────────

    def export_list(self) -> Dict[str, Any]:
        """GET /api/export/list: snapshots base_canonica_*.db + manifiestos."""
        return self._get("/api/export/list")

    def export_base_canonica(self, name: str = "", manifest: bool = False) -> bytes:
        """GET /api/export/base-canonica: descarga el .db (soporta Range).

        name: snapshot a elegir (?name=); manifest=True pide el manifiesto.
        """
        params: Dict[str, Any] = {}
        if name:
            params["name"] = name
        if manifest:
            params["manifest"] = "1"
        try:
            resp = self.client.get(
                f"{self.base_url}/api/export/base-canonica",
                headers=self._headers(),
                params=params,
            )
        except httpx.HTTPError as e:
            raise VentaAPIError(f"GET /api/export/base-canonica: {e}") from e
        if resp.status_code != 200:
            self._raise("GET", "/api/export/base-canonica", resp)
        return resp.content

    # ── Admin (refresh on-demand) ─────────────────────────────────────

    def admin_refresh(self) -> Dict[str, Any]:
        """POST /api/admin/refresh: solicita refresh del snapshot (requiere auth)."""
        return self._post("/api/admin/refresh", {})

    def admin_refresh_status(self) -> Dict[str, Any]:
        """GET /api/admin/refresh-status: estado del refresh (requiere auth)."""
        return self._get("/api/admin/refresh-status")

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "VentaAPIClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
