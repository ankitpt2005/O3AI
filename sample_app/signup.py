"""Sample Application: User Signup Validator.

Validates username, email format, and minimum password length.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

MIN_PASSWORD_LENGTH = 8
MAX_EMAIL_LENGTH = 254

# Pragmatic email check: local part, single "@", domain with at least one dot.
# Linear-time pattern (no nested quantifiers) to avoid ReDoS.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+"
)


def validate_signup(data: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Validate user signup payload."""
    errors: List[str] = []

    username = data.get("username", "").strip()
    if not username:
        errors.append("Username is required.")
    elif len(username) < 3:
        errors.append("Username must be at least 3 characters.")

    email = data.get("email", "").strip()
    if not email:
        errors.append("Email is required.")
    elif len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(email):
        errors.append("Email format is invalid (expected name@example.com).")

    password = data.get("password", "")
    if not password:
        errors.append("Password is required.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )

    return len(errors) == 0, errors
