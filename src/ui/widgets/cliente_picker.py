"""Modal compartido de búsqueda y selección de clientes.

Lo usan la sección de Búsqueda (generador de sustento) y la de Reportes de
compras, para que el control de clientes sea idéntico en ambas: búsqueda por
nombre/RUC/código, paginación, anclados (📌), recientes (🕐) y selección
múltiple (o única, según ``multiple``).
"""

from __future__ import annotations

import threading

import flet as ft

from src.core import ventas_db
from src.core.g360_theme import G360Theme
from src.core.utils import cliente_visible
from src.core.ventas_db_client import VentasDbClient


def _clave_orden(texto: str) -> str:
    """Clave alfabética insensible a mayúsculas y tildes."""
    import unicodedata

    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def badge_ruc(doc: str, n_ids: int) -> str:
    """' · RUC · N códigos' si el RUC está partido entre varios ids.

    El ERP reasignó códigos internos de cliente en el pasado (mismo
    RUC, 2 ids); sin el badge ambas mitades se ven idénticas y no se
    puede comparar su historia. RUC único → cadena vacía (sin ruido).
    """
    doc = str(doc or "").strip()
    if n_ids > 1 and len(doc) >= 8:
        return f" · {doc} · {n_ids} códigos"
    return ""


def abrir_picker_clientes(
    app,
    *,
    on_confirm,
    multiple: bool = True,
    initial=None,
    vendedor_id=None,
    fecha_desde=None,
    fecha_hasta=None,
    pinned=None,
    recent=None,
    page_size: int = 100,
) -> None:
    """Abre el modal de clientes.

    Args:
        app: instancia con ``page`` y ``show_snackbar``.
        on_confirm: callback que recibe ``[{"id", "nombre"}...]`` al confirmar.
        multiple: True permite varios clientes; False solo uno.
        initial: ids ya seleccionados (se marcan al abrir).
        vendedor_id / fecha_desde / fecha_hasta: filtros de la consulta.
        pinned / recent: ids para etiquetar y priorizar visualmente.
    """
    page = app.page
    if page is None:
        return

    cli = VentasDbClient()
    sel: set[str] = set(initial or ())
    pinned_ids: set[str] = set(pinned or ())
    recent_ids: set[str] = set(recent or ())
    checks: dict[str, ft.Checkbox] = {}
    info: dict[str, str] = {}
    pin_btns: dict[str, ft.IconButton] = {}
    state = {"page": 0, "q": "", "loading": False}
    deb = {"n": 0}

    results_col = ft.Column(
        [
            ft.Row(
                [
                    ft.ProgressRing(width=16, height=16, stroke_width=2),
                    ft.Text("Cargando clientes…", size=11, color=G360Theme.text_muted_color()),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        ],
        spacing=0,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
    status = ft.Text("Cargando clientes…", size=10, color=G360Theme.text_muted_color())

    def _refresh():
        try:
            results_col.update()
            status.update()
        except Exception:
            pass

    def _marcar(cid: str, val: bool):
        if val:
            if not multiple:
                sel.clear()
                for other, cb in checks.items():
                    if other != cid:
                        cb.value = False
            sel.add(cid)
        else:
            sel.discard(cid)

    def _toggle_pin(cid: str):
        anclado = ventas_db.toggle_pinned("clientes", cid)
        if anclado:
            pinned_ids.add(cid)
        else:
            pinned_ids.discard(cid)
        app.show_snackbar(
            ("📌 Anclado: " if anclado else "Quitado de anclados: ") + info.get(cid, cid),
            app.G360_SUCCESS if anclado else ft.Colors.ON_SURFACE_VARIANT,
        )
        btn = pin_btns.get(cid)
        if btn is not None:
            btn.icon = ft.Icons.PUSH_PIN if anclado else ft.Icons.PUSH_PIN_OUTLINED
            btn.tooltip = "Quitar ancla" if anclado else "Anclar (siempre primero)"
            try:
                btn.update()
            except Exception:
                pass

    def _pin_btn(cid: str) -> ft.IconButton:
        if cid not in pin_btns:
            pin_btns[cid] = ft.IconButton(
                icon=ft.Icons.PUSH_PIN if cid in pinned_ids else ft.Icons.PUSH_PIN_OUTLINED,
                icon_size=16,
                tooltip="Anclar (siempre primero)" if cid not in pinned_ids else "Quitar ancla",
                on_click=lambda _, c=cid: _toggle_pin(c),
            )
        return pin_btns[cid]

    def _render(cs: list):
        # Mapa de RUCs partidos: una consulta chica, cargada una sola vez.
        if not state.get("mapa_ok"):
            try:
                state["splits"] = cli.fetch_rucs_partidos()
            except Exception:
                state["splits"] = {}
            state["mapa_ok"] = True
        splits = state.get("splits") or {}
        for c in cs:
            cid = c["id"]
            if cid not in checks:
                checks[cid] = ft.Checkbox(
                    value=(cid in sel),
                    on_change=lambda e, c=cid: _marcar(c, e.control.value),
                )
            else:
                checks[cid].value = cid in sel
            info[cid] = c["nombre"]
            tag = "Anclado · " if cid in pinned_ids else "Reciente · " if cid in recent_ids else ""
            doc = str(c.get("doc", "") or "").strip()
            # Display SIEMPRE corto (68414); la clave interna sigue 00068414.
            display = (
                f"{tag}{c['nombre'][:40]} "
                f"({cliente_visible(cid)}) · "
                f"{c.get('docs', 0)} fac." + badge_ruc(doc, splits.get(doc, 0))
            )
            results_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            checks[cid],
                            ft.Text(display, size=11, expand=True, color=ft.Colors.ON_SURFACE),
                            _pin_btn(cid),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(vertical=2),
                    border=ft.border.only(bottom=ft.BorderSide(1, G360Theme.border_subtle_color())),
                )
            )
        if not cs and not results_col.controls:
            termino = (state["q"] or "").strip()
            mensaje = (
                f"No hay coincidencias para «{termino}»."
                if termino
                else "No hay clientes con ventas en estos filtros."
            )
            results_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.PERSON_SEARCH_OUTLINED,
                                size=20,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Text(
                                mensaje, size=11, color=G360Theme.text_muted_color(), expand=True
                            ),
                        ],
                        spacing=10,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(horizontal=12, vertical=20),
                )
            )

    def _merge_pinned(cs: list):
        have = {c["id"] for c in cs}
        for pid in pinned_ids:
            if pid in have:
                continue
            try:
                pc = cli.fetch_client_by_id(pid)
            except Exception:
                pc = None
            if pc:
                cs.append(pc)

    def _load(page_n: int):
        if state["loading"]:
            return
        state["loading"] = True
        q = state["q"]
        try:
            cs = cli.fetch_clientes(
                vendedor_id=vendedor_id or None,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                limit=page_size,
                offset=page_n * page_size,
                search=q or None,
            )
        except Exception as ex:
            status.value = f"Error: {ex}"
            status.color = G360Theme.error_color()
            results_col.controls.clear()
            results_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.ERROR_OUTLINE, size=18, color=G360Theme.error_color()),
                            ft.Text(
                                "No se pudo cargar la lista. Reintenta la búsqueda.",
                                size=11,
                                color=G360Theme.error_color(),
                                expand=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=ft.padding.symmetric(horizontal=12, vertical=16),
                )
            )
            state["loading"] = False
            _refresh()
            return
        if page_n == 0:
            results_col.controls.clear()
            checks.clear()
            _merge_pinned(cs)
        cs = sorted(cs, key=lambda c: _clave_orden(c.get("nombre", "")))
        _render(cs)
        n_shown = page_n * page_size + len(cs)
        if len(cs) == page_size:
            results_col.controls.append(
                ft.TextButton(
                    "Cargar más…",
                    icon=ft.Icons.EXPAND_MORE,
                    style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12, vertical=6)),
                    on_click=lambda _: _load(state["page"] + 1),
                )
            )
        status.value = f"{n_shown} cliente(s)" + (f" · filtrando: '{q}'" if q else "")
        state["page"] = page_n
        state["loading"] = False
        _refresh()

    def _on_search(e):
        state["q"] = (e.control.value or "").strip()
        deb["n"] += 1
        my = deb["n"]

        def w():
            if my == deb["n"]:
                _load(0)

        threading.Timer(0.15, w).start()

    search_field = ft.TextField(
        label="Buscar por nombre, RUC o código…",
        dense=True,
        text_size=13,
        border_radius=12,
        prefix_icon=ft.Icons.SEARCH_OUTLINED,
        expand=True,
        content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        value=state["q"],
        on_change=_on_search,
        on_submit=lambda e: (state.__setitem__("q", (e.control.value or "").strip()), _load(0)),
        autofocus=True,
    )

    def _toggle_all(val: bool):
        if val and not multiple:
            app.show_snackbar("Selección única: elige un cliente", app.G360_WARNING)
            return
        for cid, cb in checks.items():
            cb.value = val
            if val:
                sel.add(cid)
            else:
                sel.discard(cid)
        _refresh()

    def _confirm(_):
        elegidos = [{"id": cid, "nombre": info.get(cid, cid)} for cid in sel if cid in info]
        if not elegidos:
            app.show_snackbar("Selecciona al menos un cliente", app.G360_WARNING)
            return
        page.close(dlg)
        on_confirm(elegidos)

    dlg = ft.AlertDialog(
        title=ft.Row(
            [
                ft.Icon(ft.Icons.SEARCH, color=G360Theme.accent_color()),
                ft.Text(
                    "Seleccionar cliente" if not multiple else "Seleccionar clientes",
                    size=14,
                    weight=ft.FontWeight.W_700,
                ),
            ],
            spacing=8,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    search_field,
                    results_col,
                    status,
                ],
                spacing=2,
                expand=True,
            ),
            width=520,
            height=400,
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            border_radius=12,
            padding=ft.padding.only(left=8, right=8, top=2, bottom=8),
        ),
        actions=[
            ft.TextButton("Marcar todo", on_click=lambda _: _toggle_all(True)),
            ft.TextButton("Desmarcar", on_click=lambda _: _toggle_all(False)),
            ft.ElevatedButton(
                "Agregar seleccionados" if multiple else "Usar cliente",
                icon=ft.Icons.ADD if multiple else ft.Icons.CHECK,
                on_click=_confirm,
                style=ft.ButtonStyle(bgcolor=G360Theme.accent_color()),
            ),
            ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg)),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.open(dlg)
    threading.Thread(target=lambda: _load(0), daemon=True).start()
