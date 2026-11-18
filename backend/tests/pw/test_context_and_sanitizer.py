"""
Output sanitization tests
"""

from backend.pyworker.engine.context import sanitize_model_output


def test_sanitize_model_output():
    # 1. Strips ```json ... ```
    raw_json_fences = "```json\n{\"key\": \"value\"}\n```"
    assert sanitize_model_output(raw_json_fences) == '{"key": "value"}'

    # 2. Strips plain ``` ... ```
    raw_plain_fences = "```\n{\"key\": \"value\"}\n```"
    assert sanitize_model_output(raw_plain_fences) == '{"key": "value"}'

    # 3. Strips whitespace
    raw_whitespace = "   \n {\"status\": \"ok\"}  \n "
    assert sanitize_model_output(raw_whitespace) == '{"status": "ok"}'

    # 4. Handles already clean text
    clean_text = '{"status": "ok"}'
    assert sanitize_model_output(clean_text) == '{"status": "ok"}'

    # 5. Handles empty
    assert sanitize_model_output("") == ""
    assert sanitize_model_output(None) == ""
