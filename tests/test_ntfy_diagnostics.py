import logging
import unittest
from unittest.mock import Mock

import requests

from ntfy.diagnostics import endpoint, error_fields, logger, request


class DiagnosticsTests(unittest.TestCase):
    def test_endpoint_never_contains_credentials_topic_or_query(self):
        self.assertEqual(endpoint('https://user:secret@example.org/private-topic?token=hidden'),
                         'https://example.org:443')

    def test_failed_request_logs_status_without_sensitive_exception_text(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError('secret private-topic token', response=response)
        method = Mock(side_effect=error)
        with self.assertLogs(logger, logging.INFO) as captured:
            with self.assertRaises(requests.HTTPError):
                request(method, 'https://user:secret@example.org/private-topic', operation='user_test')
        output = '\n'.join(captured.output)
        self.assertIn('topic_permission', output)
        self.assertIn('403', output)
        self.assertNotIn('secret', output)
        self.assertNotIn('private-topic', output)

    def test_error_classification(self):
        self.assertEqual(error_fields(requests.Timeout())['reason'], 'timeout')
        self.assertEqual(error_fields(requests.ConnectionError())['reason'], 'connection')
        response = requests.Response()
        response.status_code = 429
        self.assertEqual(error_fields(requests.HTTPError(response=response))['reason'], 'rate_limit')

    def test_success_returns_response_and_preserves_auth(self):
        response = requests.Response()
        response.status_code = 200
        method = Mock(return_value=response)
        with self.assertLogs(logger, logging.INFO):
            self.assertIs(request(method, 'http://ntfy/topic', operation='publish', auth=('u', 'p')), response)
        self.assertEqual(method.call_args.kwargs['auth'], ('u', 'p'))


class RateLimitRetryTests(unittest.TestCase):
    @staticmethod
    def response(status, *, code=None, retry_after=None):
        import json
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({'code': code}).encode()
        if retry_after is not None:
            response.headers['Retry-After'] = retry_after
        return response

    def test_test_notification_recovers_once_without_changing_request(self):
        from unittest.mock import patch
        rejected = self.response(429, code=42901, retry_after='2')
        accepted = self.response(200)
        method = Mock(side_effect=[rejected, accepted])
        with patch('ntfy.diagnostics.time.sleep') as sleep:
            result = request(method, 'http://ntfy/topic', operation='user_test',
                             retry_rate_limit=True, data=b'test', auth=('u', 'p'))
        self.assertIs(result, accepted)
        sleep.assert_called_once_with(2)
        self.assertEqual(method.call_count, 2)
        self.assertEqual(method.call_args_list[0], method.call_args_list[1])

    def test_repeated_429_stops_after_one_retry(self):
        from unittest.mock import patch
        method = Mock(side_effect=[self.response(429), self.response(429)])
        with patch('ntfy.diagnostics.time.sleep') as sleep:
            with self.assertRaises(requests.HTTPError):
                request(method, 'http://ntfy/topic', operation='user_test', retry_rate_limit=True)
        sleep.assert_called_once_with(5)
        self.assertEqual(method.call_count, 2)

    def test_long_retry_after_and_other_limits_are_not_retried(self):
        from unittest.mock import patch
        for response in [self.response(429, retry_after='3600'),
                         self.response(429, retry_after='Infinity'),
                         self.response(429, retry_after='NaN'),
                         self.response(429, code=42902), self.response(403)]:
            with self.subTest(status=response.status_code, body=response.content):
                method = Mock(return_value=response)
                with patch('ntfy.diagnostics.time.sleep') as sleep:
                    with self.assertRaises(requests.HTTPError):
                        request(method, 'http://ntfy/topic', operation='user_test', retry_rate_limit=True)
                sleep.assert_not_called()
                method.assert_called_once()

    def test_network_timeout_is_not_retried_to_avoid_duplicate_publish(self):
        method = Mock(side_effect=requests.Timeout('unknown delivery state'))
        with self.assertRaises(requests.Timeout):
            request(method, 'http://ntfy/topic', operation='user_test', retry_rate_limit=True)
        method.assert_called_once()

    def test_background_request_does_not_sleep_on_rate_limit(self):
        method = Mock(return_value=self.response(429))
        with self.assertRaises(requests.HTTPError):
            request(method, 'http://ntfy/topic', operation='publish')
        method.assert_called_once()

    def test_http_date_retry_after(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        from ntfy.diagnostics import retry_delay
        response = self.response(429, retry_after=format_datetime(datetime.now(timezone.utc) + timedelta(seconds=6)))
        self.assertTrue(0 < retry_delay(response) <= 6)

    def test_ntfy_error_code_is_logged_without_server_message(self):
        response = self.response(429, code=42901)
        self.assertEqual(error_fields(requests.HTTPError(response=response))['ntfy_code'], 42901)
