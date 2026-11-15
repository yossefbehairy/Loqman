"""Output sanitization shared by the CEO and mission pipelines."""

import re


# ---------------------------------------------------------------------------
# Output Sanitizer: Strips Markdown code fences and extraneous whitespace
# ---------------------------------------------------------------------------
def sanitize_model_output(raw_text: str) -> str:
    """
    Strips accidental markdown code fences such as ```json ... ``` or ``` ... ```
    and leading/trailing whitespace before parsing or JSON schema validation.
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    # Strip leading ```json or ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    # Strip trailing ```
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
