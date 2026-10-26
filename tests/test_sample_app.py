"""Tests for sample signup validator."""

import unittest
from sample_app.signup import validate_signup


class TestSampleApp(unittest.TestCase):
    """Test existing baseline functionality of sample signup validator."""

    def test_valid_basic_signup(self) -> None:
        """Baseline signup passes when fields are populated."""
        payload = {
            "username": "alice",
            "email": "alice@example.com",
            "password": "secretpassword",
        }
        is_valid, errors = validate_signup(payload)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_missing_fields_fail(self) -> None:
        """Missing required fields return validation errors."""
        is_valid, errors = validate_signup({})
        self.assertFalse(is_valid)
        self.assertEqual(len(errors), 3)

    def test_short_username_fails(self) -> None:
        """Usernames under 3 characters are rejected."""
        payload = {"username": "al", "email": "a@b.com", "password": "123"}
        is_valid, errors = validate_signup(payload)
        self.assertFalse(is_valid)
        self.assertIn("at least 3 characters", errors[0])

    def test_invalid_email_fails(self) -> None:
        """Invalid email addresses are rejected."""
        payload = {
            "username": "alice",
            "email": "not-an-email",
            "password": "validpassword123",
        }
        is_valid, errors = validate_signup(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("email" in err.lower() for err in errors))

    def test_short_password_fails(self) -> None:
        """Passwords under 8 characters are rejected."""
        payload = {
            "username": "alice",
            "email": "alice@example.com",
            "password": "short",
        }
        is_valid, errors = validate_signup(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("8 characters" in err.lower() for err in errors))


if __name__ == "__main__":
    unittest.main()
