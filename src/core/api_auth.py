"""Autenticacion contra la API Go de ventas (g360-ventas-api).

El usuario/clave son los mismos de intranet: tras verificar contra
intranet.cipsa.com.pe, se hace POST /api/login para obtener el token HMAC
que autoriza las descargas (/api/data, /api/export, ...).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)


def default_api_url() -> str:
    return (os.getenv("G360_API_URL", "").strip() or "http://127.0.0.1:8090").rstrip("/")


@dataclass
class AuthResult:
    success: bool
    token: str = ""
    user: str = ""
    message: str = ""
    # "auth"       -> el servidor respondio y rechazo las credenciales
    # "transport"  -> no se pudo ni conectar (API caida / WSL dormido)
    # "server"     -> respondio algo inutil (HTTP 5xx, body no valido)
    # Distinguirlos evita rotular "Credenciales rechazadas" a un simple
    # WinError 10061, que hace creer al usuario que su clave esta mal.
    kind: str = "auth"

    @property
    def es_transporte(self) -> bool:
        return self.kind == "transport"

    @property
    def texto_credenciales(self) -> str:
        """Mensaje como lo ve el usuario, sin encubar un fallo de red."""
        if self.kind == "transport":
            return f"No se pudo conectar con la API ({self.message})"
        if self.kind == "server":
            return f"La API respondio con error ({self.message})"
        return self.message or "Credenciales rechazadas"


class APIAuthClient:
    """Cliente minimo de login contra la API Go."""

    def __init__(self, api_url: str = "", timeout: float = 10.0):
        self.api_url = (api_url or default_api_url()).rstrip("/")
        self.timeout = timeout
        self._token: Optional[str] = None
        self._user: Optional[str] = None

    def login(self, user: str, password: str) -> AuthResult:
        """POST /api/login -> token. No lanza: devuelve AuthResult."""
        user = (user or "").strip()
        if not user or not password:
            return AuthResult(success=False, message="Usuario y clave requeridos")
        try:
            resp = httpx.post(
                f"{self.api_url}/api/login",
                json={"user": user, "password": password},
                timeout=self.timeout,
            )
        except httpx.ConnectError as e:
            return AuthResult(success=False, message=f"API no disponible en {self.api_url}: {e}", kind="transport")
        except Exception as e:  # timeout, DNS, etc.
            return AuthResult(success=False, message=f"Error conectando a la API: {e}", kind="transport")
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                return AuthResult(success=False, message="Respuesta de login no valida", kind="server")
            token = str(data.get("token", ""))
            if not token:
                return AuthResult(success=False, message="Login sin token", kind="server")
            self._token = token
            self._user = str(data.get("user", user))
            log.info("Login API OK como %s", self._user)
            return AuthResult(success=True, token=token, user=self._user)
        if resp.status_code in (401, 403):
            return AuthResult(success=False, message="Credenciales rechazadas por la API")
        return AuthResult(success=False, message=f"API devolvio HTTP {resp.status_code}", kind="server")

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)
