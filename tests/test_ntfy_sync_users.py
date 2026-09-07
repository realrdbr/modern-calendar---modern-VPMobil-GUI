import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ntfy.sync_users import sync_users


class SyncUsersTests(unittest.TestCase):
    def test_syncs_publisher_and_all_readers(self):
        user = SimpleNamespace(ntfy_topic='topic', ntfy_username='user', ntfy_password='secret')
        store = Mock()
        store.notification_recipients.return_value = [SimpleNamespace(user=user)]
        service = Mock()
        self.assertEqual(sync_users(store, service), 1)
        service.ensure_server_publisher.assert_called_once_with()
        service.ensure_reader_credentials.assert_called_once_with('topic', 'user', 'secret')

    def test_database_and_provisioning_errors_are_not_silenced(self):
        store = Mock()
        store.notification_recipients.side_effect = RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            sync_users(store, Mock())
        service = Mock()
        service.ensure_server_publisher.side_effect = RuntimeError('provisioner unavailable')
        with self.assertRaisesRegex(RuntimeError, 'provisioner unavailable'):
            sync_users(Mock(), service)
