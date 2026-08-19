from __future__ import annotations


def _parse_scalar(raw_value: str):
    value = raw_value.strip()

    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        return value


def parse_simple_yaml(text: str = "") -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise ValueError("Tabs are not supported in YAML config")

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        trimmed = raw_line.strip()
        separator_index = trimmed.find(":")
        if separator_index <= 0:
            raise ValueError(f"Invalid YAML line: {trimmed}")

        key = trimmed[:separator_index].strip()
        raw_value = trimmed[separator_index + 1 :]

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if not isinstance(parent, dict):
            raise ValueError(f"Invalid YAML nesting near: {trimmed}")

        if not raw_value.strip():
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
            continue

        parent[key] = _parse_scalar(raw_value)

    return root
