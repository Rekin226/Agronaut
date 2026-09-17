"""Channel diagnostics must verify WhatsApp, without making live API calls."""
import os
import unittest
from unittest.mock import patch

from agronaut_agent import doctor as D
from agronaut_agent import whatsapp_doctor as W


class WhatsAppDoctorTests(unittest.TestCase):
    def test_expired_token_fails_with_actionable_fix(self):
        with patch.dict(os.environ, {
            "WHATSAPP_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": "123",
        }, clear=True), patch.object(W, "_get", return_value=(
            401, {"error": {"code": 190, "message": "Session has expired"}}
        )) as get:
            checks = D.check_channels()
        failed = [c for c in checks if c.status == W.FAIL]
        self.assertEqual(len(failed), 1)
        self.assertIn("24 h", failed[0].fix)
        self.assertIn("API Setup", failed[0].fix)
        self.assertIn("expired", failed[0].detail)
        get.assert_called_once()
        self.assertEqual(D.report(checks)[1], 1)

    def test_valid_token_checks_the_configured_phone_number(self):
        with patch.dict(os.environ, {
            "WHATSAPP_TOKEN": " test-token ", "WHATSAPP_PHONE_NUMBER_ID": " 123 ",
        }, clear=True), patch.object(W, "_get", return_value=(
            200, {"display_phone_number": "123", "verified_name": "Test"}
        )) as get:
            checks = D.check_channels()
        get.assert_called_once_with(
            f"{W.GRAPH}/123?fields=display_phone_number,verified_name", "test-token")
        self.assertEqual([c.status for c in checks if "WhatsApp" in c.label], [W.OK])

    def test_unreachable_network_warns_instead_of_passing_or_rejecting(self):
        with patch.dict(os.environ, {
            "WHATSAPP_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": "123",
        }, clear=True), patch.object(W, "_get", return_value=(
            0, {"error": {"message": "timed out"}}
        )):
            checks = D.check_channels()
        whatsapp = [c for c in checks if "WhatsApp" in c.label]
        self.assertEqual(len(whatsapp), 1)
        self.assertEqual(whatsapp[0].status, W.WARN)
        self.assertIn("not verified", whatsapp[0].label)
        self.assertIn("timed out", whatsapp[0].detail)

    def test_transient_api_errors_are_not_reported_as_invalid_tokens(self):
        for status in (429, 500, 503):
            with self.subTest(status=status), patch.dict(os.environ, {
                "WHATSAPP_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": "123",
            }, clear=True), patch.object(W, "_get", return_value=(
                status, {"error": {"message": "try again later"}}
            )):
                checks = D.check_channels()
            whatsapp = [c for c in checks if "WhatsApp" in c.label]
            self.assertEqual(len(whatsapp), 1)
            self.assertEqual(whatsapp[0].status, W.WARN)
            self.assertIn("not verified", whatsapp[0].label)

    def test_auth_rejections_name_the_token_while_other_4xx_name_the_config(self):
        for status, marker in ((401, "token rejected"), (400, "rejected the check")):
            with self.subTest(status=status), patch.dict(os.environ, {
                "WHATSAPP_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": "123",
            }, clear=True), patch.object(W, "_get", return_value=(
                status, {"error": {"message": "Unsupported get request"}}
            )):
                checks = D.check_channels()
            whatsapp = [c for c in checks if "WhatsApp" in c.label]
            self.assertEqual(len(whatsapp), 1)
            self.assertEqual(whatsapp[0].status, W.FAIL)
            self.assertIn(marker, whatsapp[0].label)

    def test_missing_phone_number_does_not_attempt_a_network_check(self):
        with patch.dict(os.environ, {"WHATSAPP_TOKEN": "test-token"}, clear=True), \
                patch.object(W, "_get") as get:
            checks = D.check_channels()
        whatsapp = [c for c in checks if "WhatsApp" in c.label]
        self.assertEqual(whatsapp[0].status, W.WARN)
        self.assertIn("WHATSAPP_PHONE_NUMBER_ID", whatsapp[0].detail)
        get.assert_not_called()

    def test_no_token_stays_skipped_without_a_network_check(self):
        with patch.dict(os.environ, {"WHATSAPP_TOKEN": "  "}, clear=True), \
                patch.object(W, "_get") as get:
            checks = D.check_channels()
        whatsapp = [c for c in checks if "WhatsApp" in c.label]
        self.assertEqual(whatsapp[0].status, D.SKIP)
        self.assertEqual(whatsapp[0].label, "WhatsApp not configured")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
