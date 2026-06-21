from dataclasses import dataclass


@dataclass
class Edit:
    """A byte-range replacement in the original source."""
    start_byte: int
    end_byte: int
    new_text: str


def apply_edits(source: str, edits: list[Edit]) -> str:
    """Apply a list of non-overlapping edits to source, sorted by position."""
    edits = sorted(edits, key=lambda e: e.start_byte)
    result: list[str] = []
    cursor = 0
    for edit in edits:
        result.append(source[cursor:edit.start_byte])
        result.append(edit.new_text)
        cursor = edit.end_byte
    result.append(source[cursor:])
    return "".join(result)
