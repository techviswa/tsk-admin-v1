import unittest

import error_reporting


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__("client error")
        self.status_code = status_code


class ErrorReportingTests(unittest.TestCase):
    def test_personal_data_and_secrets_are_removed(self):
        event = {
            "user": {"email": "admin@example.com", "ip_address": "1.2.3.4"},
            "server_name": "host",
            "request": {"method": "POST", "url": "https://admin.example.com/api/auth/reset?token=abc", "headers": {"cookie": "access_token=x"}, "data": {"password": "p"}},
            "message": "Login failed for owner@example.com from +91 98450 12345 with Bearer abc.def",
            "exception": {"values": [{"value": "key rzp_live_ABC123 rejected", "stacktrace": {"frames": [{"vars": {"secret": "s"}, "context_line": "x = secret"}]}}]},
            "extra": {"body": "{...}"},
        }
        out = error_reporting.scrub_event(event)
        self.assertNotIn("user", out)
        self.assertNotIn("server_name", out)
        self.assertEqual(out["request"], {"method": "POST", "url": "https://admin.example.com/api/auth/reset"})
        self.assertEqual(out["message"], "Login failed for [email] from [number] with Bearer [redacted]")
        self.assertEqual(out["exception"]["values"][0]["value"], "key rzp_live_[redacted] rejected")
        self.assertEqual(out["exception"]["values"][0]["stacktrace"]["frames"][0], {})
        self.assertNotIn("extra", out)

    def test_client_errors_are_not_reported(self):
        self.assertIsNone(error_reporting.scrub_event({}, {"exc_info": (HttpError, HttpError(404), None)}))
        self.assertIsNotNone(error_reporting.scrub_event({}, {"exc_info": (HttpError, HttpError(500), None)}))

    def test_off_without_dsn(self):
        self.assertFalse(error_reporting.init_error_reporting({}))


if __name__ == "__main__":
    unittest.main()
