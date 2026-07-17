import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException


AUTH_PATH = Path(__file__).resolve().parents[1] / "interface functions" / "auth.py"
AUTH_SPEC = importlib.util.spec_from_file_location("bisnes_auth_validation", AUTH_PATH)
auth = importlib.util.module_from_spec(AUTH_SPEC)
assert AUTH_SPEC.loader is not None
AUTH_SPEC.loader.exec_module(auth)


class AuthIdentityValidationTests(unittest.TestCase):
    def test_valid_phone_is_normalized_to_e164_for_selected_country(self):
        result = auth._validate_phone_number("US", "4155552671")
        self.assertEqual(result["country_iso"], "US")
        self.assertEqual(result["country_code"], "+1")
        self.assertEqual(result["phone_e164"], "+14155552671")

    def test_phone_rejects_non_digits_before_parsing(self):
        with self.assertRaises(HTTPException) as error:
            auth._validate_phone_number("MY", "12 34-567")
        self.assertEqual(error.exception.detail, "Phone number can only contain digits")

    def test_phone_rejects_invalid_number_range(self):
        with self.assertRaises(HTTPException) as error:
            auth._validate_phone_number("US", "4151234567")
        self.assertIn("valid phone number", error.exception.detail)

    def test_name_requires_both_parts_and_allows_international_letters(self):
        self.assertEqual(auth._resolve_identity("李", "小龍"), ("李", "小龍", "李 小龍"))
        with self.assertRaises(HTTPException) as error:
            auth._resolve_identity("Ada", "")
        self.assertEqual(error.exception.detail, "Last name is required")

    def test_name_rejects_digits_and_symbols(self):
        with self.assertRaises(HTTPException) as error:
            auth._resolve_identity("Ada123", "Lovelace")
        self.assertIn("can only contain letters", error.exception.detail)

    def test_resend_cooldown_reports_remaining_seconds(self):
        now = datetime(2026, 7, 15, 8, 0, 0)
        pending = {"resend_available_at": now + timedelta(seconds=60)}
        self.assertEqual(auth._otp_resend_retry_after_seconds(pending, now), 60)
        self.assertEqual(auth._otp_resend_retry_after_seconds(pending, now + timedelta(seconds=60)), 0)

    def test_otp_email_is_clean_and_contains_the_code(self):
        rendered = auth.build_otp_email_html("123456", "ignored", "ignored", "ignored", "en")
        self.assertIn("Your bisnes.ai verification code", rendered)
        self.assertIn(">123456<", rendered)
        self.assertIn("This code will expire in 10 minutes.", rendered)
        self.assertNotIn("Request ID", rendered)

    def test_password_reset_email_uses_a_specific_safe_template(self):
        rendered = auth.build_otp_email_html(
            "654321", "ignored", "ignored", "ignored", "en", purpose="password_reset"
        )
        self.assertIn("Your bisnes.ai password reset code", rendered)
        self.assertIn(">654321<", rendered)
        self.assertIn("If you didn’t request a password reset", rendered)
        self.assertNotIn("continue signing in", rendered)


if __name__ == "__main__":
    unittest.main()
