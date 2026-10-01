"""Composable section for the main recognition workflow.

Sections live inside one workspace surface instead of nesting a separate
shadowed card for every part of the task. This keeps the flow scannable while
preserving one shared G360 surface and semantic color tokens.
"""

from __future__ import annotations

import flet as ft

from src.core.g360_theme import G360Theme


def workflow_section(
    title: str,
    icon,
    content: ft.Control,
    *,
    description: str = "",
    action: ft.Control | None = None,
    divider: bool = True,
) -> ft.Column:
    """Build a labelled, non-card section within the primary workflow card."""
    icon_box = ft.Container(
        content=ft.Icon(icon, size=17, color=G360Theme.accent_color()),
        width=34,
        height=34,
        alignment=ft.alignment.center,
        bgcolor=G360Theme.accent_soft_color(0.10),
        border_radius=10,
    )
    heading = [
        ft.Text(title, size=13, weight=ft.FontWeight.W_700, color=G360Theme.text_primary_color()),
    ]
    if description:
        heading.append(ft.Text(description, size=10, color=G360Theme.text_muted_color()))
    controls = [
        ft.Row(
            [
                icon_box,
                ft.Column(heading, spacing=2, expand=True),
                *([action] if action is not None else []),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        content,
    ]
    if divider:
        controls.append(ft.Divider(height=1, color=G360Theme.border_subtle_color()))
    return ft.Column(controls, spacing=10, tight=True)
