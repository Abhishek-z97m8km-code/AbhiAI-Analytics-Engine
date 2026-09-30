"""Parse explicitly supplied local paths without interpreting shell syntax."""

from pathlib import Path


_UNSAFE_SHELL_TEXT = ("$", "`", "|", ";", "&&", "||", "<", ">", "*", "?", "[")


def parse_local_path(value, home_directory=None):
    """Return a normalized Path or a friendly error for an explicit path.

    This intentionally recognizes only outer quotes plus Finder-style escaped
    spaces/backslashes. It is not a shell parser and never expands variables,
    globs, or command substitutions.
    """

    if isinstance(value, Path):
        return value, None
    if not isinstance(value, str) or not value.strip():
        return None, "Tell me which file to open."
    text = value.strip()
    if any(token in text for token in _UNSAFE_SHELL_TEXT):
        return None, "That path contains unsupported shell syntax."
    if text[0] in {"'", '"'} or text[-1] in {"'", '"'}:
        if len(text) < 2 or text[0] != text[-1] or text[0] not in {"'", '"'}:
            return None, "That path has unmatched quotes."
        text = text[1:-1]
    if not text:
        return None, "Tell me which file to open."

    normalized, index = [], 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in {" ", "\\"}:
                normalized.append(following)
                index += 2
                continue
        normalized.append(character)
        index += 1
    text = "".join(normalized)
    home = Path(home_directory) if home_directory is not None else Path.home()
    if text == "~":
        return home, None
    if text.startswith("~/"):
        return home / text[2:], None
    return Path(text), None