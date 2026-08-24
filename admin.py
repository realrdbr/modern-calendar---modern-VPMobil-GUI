"""Lokale Admin-Befehle. Dieses Programm wird nur auf dem Server ausgeführt."""

from __future__ import annotations

import argparse
from datetime import date
from getpass import getpass
import os
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

from accounts import AccountStore
from cli_runtime import maybe_delegate_to_vp_container, running_in_container, uses_internal_mariadb
from ntfy.service import NtfyService
from subscriptions import SubscriptionNotifier
from vp_data import fetch_plan


ROOT = Path(__file__).resolve().parent

load_dotenv()

def store() -> AccountStore:
    database_url = os.getenv("APP_DATABASE_URL", "").strip()
    use_sqlite_fallback = uses_internal_mariadb() and not running_in_container()

    if not database_url and not use_sqlite_fallback and os.getenv("DB_HOST") and os.getenv("DB_USER") and os.getenv("DB_NAME"):
        password = quote(os.getenv("DB_PASSWORD", ""), safe="")
        user = quote(os.getenv("DB_USER", ""), safe="")
        host = os.getenv("DB_HOST", "")
        port = os.getenv("DB_PORT", "3306")
        name = quote(os.getenv("DB_NAME", ""), safe="")
        database_url = f"mariadb://{user}:{password}@{host}:{port}/{name}"

    if database_url and not use_sqlite_fallback:
        return AccountStore(database_url, os.getenv("APP_ENCRYPTION_KEY", ""))

    return AccountStore(Path(os.getenv("APP_DATABASE", ROOT / "data" / "vpmobil.sqlite3")), os.getenv("APP_ENCRYPTION_KEY", ""))


def main() -> None:
    maybe_delegate_to_vp_container("admin.py")
    parser = argparse.ArgumentParser(description="VpMobil-Nutzerverwaltung (nur Server-Admin)")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Legt Nutzer und privaten ntfy-Zugang an")
    create.add_argument("username")
    create.add_argument("--class", dest="class_name", required=True)
    create.add_argument("--pin", help="Genau vier Ziffern. Sicherer ist, den Parameter wegzulassen und den Prompt zu nutzen.")
    reset = commands.add_parser("reset-pin", help="Setzt eine PIN zurück")
    reset.add_argument("username")
    reset.add_argument("--pin", help="Sicherer ist, den Parameter wegzulassen und den Prompt zu nutzen.")
    active = commands.add_parser("set-active", help="Aktiviert/deaktiviert einen Nutzer")
    active.add_argument("username")
    active.add_argument("--active", choices=("true", "false"), required=True)
    delete = commands.add_parser("delete-user", help="Löscht einen Nutzer und seinen ntfy-Zugang")
    delete.add_argument("username")
    test = commands.add_parser("send-test", help="Sendet eine Testbenachrichtigung an einen Nutzer")
    test.add_argument("username")
    test.add_argument("--type", choices=("morning", "change", "next"), required=True,
                      help="morning=Tagesübersicht, change=Planänderung, next=Nächster Unterricht")
    test.add_argument("--block", type=int, choices=(1, 2, 3),
                      help="Ausgangsblock für next, z.B. 1 für die Meldung zu Block 2")
    args = parser.parse_args()
    account_store = store()
    if args.command in {"create-user", "reset-pin"} and not args.pin:
        args.pin = getpass("Vierstellige PIN: ")
    if args.command == "create-user":
        service = NtfyService(ROOT)
        service.ensure_running()
        topic, ntfy_username, ntfy_password = service.create_reader()
        try:
            user = account_store.create_user(args.username, args.pin, args.class_name, ntfy_topic=topic, ntfy_username=ntfy_username, ntfy_password=ntfy_password)
        except Exception:
            service.delete_reader(ntfy_username)
            raise
        print(f"Nutzer {user.username} für Klasse {user.class_name} angelegt. ntfy-Topic: {user.ntfy_topic}")
    elif args.command == "reset-pin":
        account_store.set_pin(args.username, args.pin)
        print("PIN geändert.")
    elif args.command == "delete-user":
        user = account_store.get_user(args.username)
        account_store.set_active(user.username, False)
        service = NtfyService(ROOT)
        service.ensure_running()
        service.delete_reader(user.ntfy_username)
        account_store.delete_user(user.username)
        print(f"Nutzer {user.username} und sein ntfy-Zugang wurden gelöscht.")
    elif args.command == "send-test":
        user = account_store.get_user(args.username)
        if not user.active:
            raise ValueError("Der Nutzer ist deaktiviert.")
        service = NtfyService(ROOT)
        service.ensure_running()
        plan = fetch_plan(date.today())
        SubscriptionNotifier(account_store, service.internal_url).send_test(
            user, account_store.get_subjects(user.id), plan, args.type, args.block,
        )
        print(f"Testbenachrichtigung ({args.type}) an {user.username} gesendet.")
    else:
        account_store.set_active(args.username, args.active == "true")
        print("Nutzerstatus geändert.")


if __name__ == "__main__":
    main()


# python admin.py send-test lisa --type next --block 1
# python admin.py send-test lisa --type morning