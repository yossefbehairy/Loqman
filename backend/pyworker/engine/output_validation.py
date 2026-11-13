"""Validate the documented subset of output schemas without dependencies."""

import math


def validate_output(value, schema=None, path="$", depth=0):
    if depth > 32:
        raise ValueError("Output exceeds maximum nesting depth.")
    if schema is None:
        if not isinstance(value, dict) or not value:
            raise ValueError("Agent output must be a nonempty JSON object.")
        return
    if not isinstance(schema, dict):
        raise ValueError("Output schema must be an object.")
    supported = {"type", "properties", "required", "additionalProperties", "items", "enum",
                 "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
                 "title", "description", "$schema", "default"}
    if set(schema) - supported:
        raise ValueError("Output schema contains unsupported validation keywords.")
    types = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float) and math.isfinite(value),
        "null": value is None,
    }
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or not types.get(kind, False)):
        raise ValueError(f"{path} has an unexpected type.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must match an allowed value.")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path} is missing required fields: {', '.join(sorted(missing))}.")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{path} contains unexpected fields.")
        for name, item in value.items():
            if name in properties:
                validate_output(item, properties[name], f"{path}.{name}", depth+1)
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_output(item, schema["additionalProperties"], f"{path}.{name}", depth+1)
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            raise ValueError(f"{path} has an invalid item count.")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_output(item, schema["items"], f"{path}[{index}]", depth+1)
    if isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            raise ValueError(f"{path} has an invalid length.")
    if type(value) in (int, float):
        if not math.isfinite(value) or not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            raise ValueError(f"{path} is outside the allowed numeric range.")
