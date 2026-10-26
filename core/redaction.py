"""Secret detection and redaction shared by the audit logger and safety governor."""

import re
from typing import Any, List

_SECRET_PATTERNS = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}")),
    ("url_credential", re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token)=)[^&\s\"']+")),
    ("assigned_secret", re.compile(
        r"(?i)\b(password|passwd|pwd|api[_\-]?key|secret|token|credential)s?\b(\s*[:=]\s*)(\"[^\"]+\"|'[^']+'|\S+)"
    )),
]

REDACTED = "[REDACTED]"


def find_secrets(text: str) -> List[str]:
    """Return the names of secret patterns present in text."""
    if not text:
        return []
    return [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]


def redact_text(text: str) -> str:
    if not text:
        return text
    for name, pattern in _SECRET_PATTERNS:
        if name == "url_credential":
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        elif name == "assigned_secret":
            text = pattern.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact secrets inside strings, dicts, lists and tuples."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value
