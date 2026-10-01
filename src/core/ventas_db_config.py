# -*- coding: utf-8 -*-
"""Configuración y estado de usuario de la DB SQLite local del sustentor.

Extraída de src/core/ventas_db.py para mantener un archivo pequeño y
especializado: rutas de datos, allowlist de líneas de producto, preferencias
(credenciales intranet), anclados (pinned) y recientes de pickers.

Se re-exporta desde ventas_db.py (import * explicito) para no romper los
call-sites existentes: ``from src.core import ventas_db`` y
``from src.core.ventas_db import X`` siguen funcionando igual.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# ── Paths ───────────────────────────────────────────────────────────────────
APP_DIR_NAME = "g360-erp-nc-sustentor"


def data_dir() -> Path:
    override = os.getenv("G360_DATA_DIR")
    if override:
        return Path(override)
    base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_DIR_NAME / "data"


def db_path() -> Path:
    return data_dir() / "historial.db"


def raw_dir() -> Path:
    return data_dir() / "raw"


def estado_path() -> Path:
    """Sidecar de estado local: oc_alias + day_state (viaja en el cartucho).

    Vive al lado de historial.db pero es un archivo aparte: nunca forma
    parte del contrato de forma del cartucho.
    """
    return data_dir() / "estado_sustentor.db"


def config_file_path() -> Path:
    return data_dir() / "config.json"


# ── Allowlist de lineas (port de config.rs) ─────────────────────────────────

DEFAULT_ALLOWED_LINES = [
    "01",
    "02",
    "09",
    "11",
    "14",
    "72",
    "73",
    "75",
    "76",
    "77",
    "78",
    "79",
    "81",
    "85",
    "99",
    "MA",
    "CA",
    "AD",
    "CB",
    "CC",
    "CD",
    "CE",
    "CF",
    "CG",
]

_LINES_CACHE: list[str] | None = None


def allowed_lines() -> list[str]:
    global _LINES_CACHE
    if _LINES_CACHE is not None:
        return _LINES_CACHE
    lines = list(DEFAULT_ALLOWED_LINES)
    try:
        cfg = load_app_config()
        cfg_lines = cfg.get("allowed_lines") or []
        if cfg_lines:
            lines = [str(x) for x in cfg_lines]
    except Exception:
        pass
    _LINES_CACHE = lines
    return lines


def is_allowed_line(id_linea: str) -> bool:
    if not id_linea or len(id_linea) < 2:
        return False
    suffix = id_linea[-2:].upper()
    return suffix in allowed_lines()


def active_line_sql(col: str) -> str:
    """Predicado SQL: la col id_linea (formato '01AD') termina en linea activa.

    La DB guarda el codigo con prefijo de sucursal ('01AD' = linea 'AD'),
    por eso se compara con SUBSTR(-2), igual que is_allowed_line().
    """
    codes = ",".join(f"'{str(x).upper()}'" for x in allowed_lines())
    return f"UPPER(SUBSTR({col}, -2)) IN ({codes})"


def reset_allowed_lines_cache() -> None:
    global _LINES_CACHE
    _LINES_CACHE = None


# ── Config local (credenciales intranet + preferencias) ─────────────────────


def load_app_config() -> dict:
    p = config_file_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_app_config(cfg: dict) -> None:
    p = config_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    reset_allowed_lines_cache()


# ── Anclados (favoritos) por usuario ─────────────────────────────────────────
# Permite que un usuario fije un vendedor/cliente concreto y lo vea siempre
# primero en la cascada (p.ej. quien solo atiende una cuenta o una sucursal).
# Se guarda en config.json bajo cfg["pinned"][<usuario>] para no mezclar entre
# operadores de la misma PC.


def _pinned_user_key(user: str | None = None) -> str:
    """Clave del usuario actual: el de intranet (sin clave) o 'default'."""
    if user is None:
        try:
            from src.core.capture_service import CaptureService

            user, _ = CaptureService.credentials()
        except Exception:
            user = ""
    return (user or "").strip() or "default"


def load_pinned(user: str | None = None) -> dict:
    """Devuelve {'clientes': [ids], 'vendedores': [ids]} del usuario dado."""
    cfg = load_app_config()
    pin = cfg.get("pinned", {}) or {}
    data = pin.get(_pinned_user_key(user), {}) or {}
    return {
        "clientes": list(data.get("clientes", [])),
        "vendedores": list(data.get("vendedores", [])),
    }


def save_pinned(clientes: list[str], vendedores: list[str], user: str | None = None) -> None:
    """Persiste la lista de anclados del usuario (reemplaza la anterior)."""
    cfg = load_app_config()
    pinned = cfg.setdefault("pinned", {})
    pinned[_pinned_user_key(user)] = {
        "clientes": list(dict.fromkeys(clientes)),
        "vendedores": list(dict.fromkeys(vendedores)),
    }
    save_app_config(cfg)


def toggle_pinned(kind: str, ident: str, user: str | None = None) -> bool:
    """Ancla/quita un id en 'clientes' o 'vendedores'. Retorna True si quedo anclado."""
    if kind not in ("clientes", "vendedores"):
        raise ValueError(f"kind invalido: {kind}")
    cur = load_pinned(user)
    lst = cur[kind]
    if ident in lst:
        lst.remove(ident)
        anclado = False
    else:
        lst.append(ident)
        anclado = True
    save_pinned(cur["clientes"], cur["vendedores"], user=user)
    return anclado


# ── Recientes (ultimos usados) ────────────────────────────────────────────────
# Guarda los ultimos N clientes/vendedores usados en el picker.
# Se persiste en config.json bajo cfg["recent"][<usuario>] con la misma
# clave de usuario que pinned. Se usa para mostrar los más recientes
# primero en el picker modal.

_RECENT_MAX = 15


def load_recent(kind: str, user: str | None = None) -> list[str]:
    """Devuelve los ids recientes de 'clientes' o 'vendedores'."""
    cfg = load_app_config()
    rec = cfg.get("recent", {}) or {}
    data = rec.get(_pinned_user_key(user), {}) or {}
    return list(data.get(kind, []))


def push_recent(kind: str, ident: str, user: str | None = None) -> None:
    """Registra un id como usado recientemente (mantiene max _RECENT_MAX)."""
    cfg = load_app_config()
    rec = cfg.setdefault("recent", {})
    data = rec.setdefault(_pinned_user_key(user), {})
    lst = list(data.get(kind, []))
    if ident in lst:
        lst.remove(ident)
    lst.insert(0, ident)
    data[kind] = lst[:_RECENT_MAX]
    save_app_config(cfg)
