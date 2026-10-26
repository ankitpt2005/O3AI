"""Security controls and untrusted input defenses for O3AI.

Treats all GitLab issue text as hostile/untrusted input.
Provides strict title length validation, injection defense, and path safety.
"""

from __future__ import annotations
import re
from pathlib import Path


MAX_TITLE_WORDS = 35
MAX_INPUT_CHARS = 100_000

# Disallowed prompt escape sequences and injection markers
SUSPICIOUS_PROMPT_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
    r"dan\s+mode",
    r"<\s*script[^>]*>",
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__",
]

# Patterns suggesting multiple unrelated topics merged into one issue
MULTI_TOPIC_INDICATORS = [
    r"\balso\b.*\band\s+then\b",
    r"\bfurthermore\b.*\bseparately\b",
    r"\badditionally\b.*\bas\s+well\s+as\b",
    r";\s*and\s+also\b",
]


class SecurityValidationError(ValueError):
    """Raised when issue content fails security or integrity constraints."""
    pass


def count_words(text: str) -> int:
    """Return count of whitespace-delimited words in string, preserving unicode words."""
    tokens = [tok for tok in text.strip().split() if tok]
    return len(tokens)


def validate_issue_title(title: str) -> None:
    """Validate that the issue title is present, <= 35 words, and single-topic.

    Raises:
        SecurityValidationError: If the title violates any constraint.
    """
    if not isinstance(title, str) or not title.strip():
        raise SecurityValidationError("Issue title cannot be empty.")

    cleaned = title.strip()
    word_count = count_words(cleaned)
    if word_count > MAX_TITLE_WORDS:
        raise SecurityValidationError(
            f"Issue title has {word_count} words (exceeds limit of {MAX_TITLE_WORDS})."
        )

    for pattern in MULTI_TOPIC_INDICATORS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            raise SecurityValidationError(
                "Issue title appears multi-topic. One issue must equal one single topic."
            )


def validate_goal_text(goal_text: str) -> None:
    """Validate goal text payload for non-emptiness and integrity.

    Raises:
        SecurityValidationError: If goal text is empty or invalid.
    """
    if not isinstance(goal_text, str) or not goal_text.strip():
        raise SecurityValidationError("Goal text cannot be empty.")


def sanitize_untrusted_text(raw_text: str) -> str:
    """Sanitize raw untrusted text by stripping control chars and bounding length."""
    if not raw_text:
        return ""
    # Cap huge input to prevent memory exhaustion or regex DoS
    capped = raw_text[:MAX_INPUT_CHARS]
    # Strip null bytes and non-printable control characters except newline and tab
    sanitized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", capped)
    # Neutralize prompt boundary breakout tokens
    sanitized = sanitized.replace("<<<UNTRUSTED_INPUT_START>>>", "[[ESCAPED_DELIMITER]]")
    sanitized = sanitized.replace("<<<UNTRUSTED_INPUT_END>>>", "[[ESCAPED_DELIMITER]]")
    return sanitized.strip()


def frame_for_ai_prompt(untrusted_text: str) -> str:
    """Wrap untrusted user content inside safe boundary markers with strict system framing."""
    clean = sanitize_untrusted_text(untrusted_text)
    return (
        "<<<UNTRUSTED_INPUT_START>>>\n"
        f"{clean}\n"
        "<<<UNTRUSTED_INPUT_END>>>"
    )


def assert_safe_path(target_path: Path, base_dir: Path) -> Path:
    """Ensure target_path does not escape base_dir (directory traversal defense).

    Raises:
        SecurityValidationError: If resolved path escapes base_dir.
    """
    resolved_base = base_dir.resolve()
    resolved_target = target_path.resolve()
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError:
        raise SecurityValidationError(
            f"Path traversal detected: {resolved_target} is outside {resolved_base}"
        )
    return resolved_target
