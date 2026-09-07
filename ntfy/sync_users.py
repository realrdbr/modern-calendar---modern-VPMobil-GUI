"""Synchronize accounts inside VP; fail visibly if DB/provisioning is unavailable."""
from ntfy.diagnostics import configure_logging, emit


def sync_users(store, service):
    service.ensure_server_publisher()
    count = 0
    for recipient in store.notification_recipients():
        user = recipient.user
        service.ensure_reader_credentials(user.ntfy_topic, user.ntfy_username, user.ntfy_password)
        count += 1
    emit("ntfy.users_synced", users=count)
    return count


def main():
    from main import ROOT, build_store
    from ntfy.service import NtfyService

    configure_logging()
    sync_users(build_store(), NtfyService(ROOT))


if __name__ == "__main__":
    main()
