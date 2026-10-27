"""Sample Application: User Signup Validator.

Deliberately missing email format validation and password length checking.
Will be enhanced through O3AI Working Tower flow.
"""

from __future__ import annotations
from typing import Dict, List, Tuple


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
    # Intentionally missing regex email check

    password = data.get("password", "")
    if not password:
        errors.append("Password is required.")
    # Intentionally missing min length check

    return len(errors) == 0, errors
