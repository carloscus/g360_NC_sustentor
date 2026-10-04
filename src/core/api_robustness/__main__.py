"""Entry point: python -m src.core.api_robustness <subcomando>

Subcomandos:
  check      Diagnóstico completo (servidor + cliente)
  recovery   Comandos de recuperación (refresh, restore, sync, find-server)
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="api_robustness",
        description="Diagnóstico y recuperación del API G360",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── check ──────────────────────────────────────────────────────────
    p_check = sub.add_parser("check", help="Diagnóstico de estado API")
    p_check.add_argument("--server", action="store_true", help="Solo chequeos de servidor (WSL)")
    p_check.add_argument("--client", action="store_true", help="Solo chequeos de cliente (HTTP)")
    p_check.add_argument("--json", action="store_true", help="Output JSON")
    p_check.add_argument("--url", default="", help="URL del servidor")

    # ── recovery ───────────────────────────────────────────────────────
    p_rec = sub.add_parser("recovery", help="Comandos de recuperación")
    rec_sub = p_rec.add_subparsers(dest="action", required=True)

    p_refresh = rec_sub.add_parser("refresh", help="Forzar refresh del snapshot")
    p_refresh.add_argument("--url", default="", help="URL del servidor")

    p_restore = rec_sub.add_parser("restore", help="Restaurar DB desde cartucho")
    p_restore.add_argument("--db", default="", help="Ruta de la DB local")

    p_sync = rec_sub.add_parser("sync", help="Sync forzado (ignora frescura)")
    p_sync.add_argument("--url", default="", help="URL del servidor")
    p_sync.add_argument("--user", default="", help="Usuario intranet")
    p_sync.add_argument("--password", default="", help="Contraseña intranet")

    p_find = rec_sub.add_parser("find-server", help="Buscar servidor en la red")
    p_find.add_argument("--subnet", default="", help="Subnet a escanear")

    args = parser.parse_args()

    if args.command == "check":
        from src.core.api_robustness.check import check, _is_server_machine

        is_srv = _is_server_machine()
        if args.server and not is_srv:
            print("ERROR: Esta máquina no es servidor (no tiene WSL + repo API).", file=sys.stderr)
            return 1
        if args.client and is_srv:
            print("ERROR: Esta máquina es servidor. Usar --server o sin flags.", file=sys.stderr)
            return 1
        url = args.url or __import__("os").getenv("G360_API_URL", "http://127.0.0.1:8090")
        result = check(server_url=url, json_output=args.json)
        if args.json:
            import json
            from dataclasses import asdict

            print(json.dumps(asdict(result), indent=2, default=str))
        else:
            print(f"=== DIAGNÓSTICO [{result.severity.upper()}] ===")
            print(result.summary)
            if result.recommendations:
                print("\nRecomendaciones:")
                for i, rec in enumerate(result.recommendations, 1):
                    print(f"  {i}. {rec}")
            if result.client and result.client.errors:
                print("\nErrores:")
                for e in result.client.errors:
                    print(f"  • {e}")
            if result.client:
                print("\nDetalles cliente:")
                print(f"  Servidor: {result.client.server_url}")
                print(f"  Conectado: {result.client.http_8090_reachable}")
                print(f"  Health OK: {result.client.health_ok}")
                print(
                    f"  Token válido: {result.client.token_valid} "
                    f"(edad: {result.client.token_age_hours:.1f}h)"
                )
                print(f"  Filas API: {result.client.status_filas:,}")
                print(f"  Filas local: {result.client.local_filas:,}")
                print(f"  Capturado en API: {result.client.status_capturado_en}")
                print(f"  Fecha máx local: {result.client.local_fecha_max}")
                print(f"  Antigüedad snapshot: {result.client.desfase_horas:.1f}h")
                if result.client.dias_desfasados:
                    print(f"  Días desfasados: {', '.join(result.client.dias_desfasados)}")
        return 0 if result.severity != "critical" else 1

    elif args.command == "recovery":
        from src.core.api_robustness import recovery

        if args.action == "refresh":
            r = recovery.trigger_refresh_snapshot(args.url)
        elif args.action == "restore":
            from pathlib import Path

            db_path = Path(args.db) if args.db else None
            r = recovery.restore_from_backup(db_path)
        elif args.action == "sync":
            r = recovery.force_sync(args.url, args.user, args.password)
        elif args.action == "find-server":
            found = recovery.find_server_on_network(args.subnet or None)
            if found:
                print(f"Servidores encontrados ({len(found)}):")
                for s in found:
                    status = "OK" if s["healthy"] else "NO RESPONDE"
                    print(f"  {s['url']}  [{status}]  DB: {s.get('db', '?')}")
            else:
                print("No se encontró ningún servidor con API en :8090.")
            return 0
            return 0
        else:
            print(f"Unknown recovery action: {args.action}", file=sys.stderr)
            return 1
        print(f"{'✓' if r.success else '✗'} [{r.action}] {r.detail}")
        if r.error:
            print(f"Error: {r.error}")
        return 0 if r.success else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
