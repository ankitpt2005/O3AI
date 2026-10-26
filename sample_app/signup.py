"""Sample Application: User Signup Validator.

Updated by O3AI Working Tower: Email and password validation implemented.
"""

from __future__ import annotations
import re
from typing import Dict, List, Tuple

EMAIL_REGEX = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


def validate_signup(data: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Validate user signup payload with strict email and password checks."""
    errors: List[str] = []

    username = data.get("username", "").strip()
    if not username:
        errors.append("Username is required.")
    elif len(username) < 3:
        errors.append("Username must be at least 3 characters.")

    email = data.get("email", "").strip()
    if not email:
        errors.append("Email is required.")
    elif not re.match(EMAIL_REGEX, email):
        errors.append("Invalid email address format.")

    password = data.get("password", "")
    if not password:
        errors.append("Password is required.")
    elif len(password) < 8:
        errors.append("Password must be at least 8 characters.")

    return len(errors) == 0, errors
