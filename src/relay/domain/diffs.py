"""LOG-4 · line diffs between two versions.

``difflib`` rather than a dependency: the diff is displayed, never applied, so
there is nothing to be gained from a patch format and a lot to be lost from
carrying one (a rollback creates a *new version from old content*, so it never
needs to reconstruct anything from a patch).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import StrEnum


class LineOp(StrEnum):
    KEEP = "keep"
    ADD = "add"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class DiffLine:
    op: LineOp
    text: str
    #: 1-based line numbers, None where the line does not exist on that side.
    old_no: int | None
    new_no: int | None


def line_diff(old: str, new: str) -> tuple[DiffLine, ...]:
    """A flat, renderable diff.

    ``keepends=False`` and no context window: the caller decides how much to
    show. Collapsing runs of KEEP lines in the service would make the return
    value lossy for no benefit — the frontend is what knows the viewport.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    out: list[DiffLine] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                out.append(
                    DiffLine(LineOp.KEEP, old_lines[i1 + offset], i1 + offset + 1, j1 + offset + 1)
                )
        else:
            # A replace is emitted as removals then additions rather than as a
            # paired op: the renderer can pair them by position if it wants to,
            # and a three-op vocabulary is one fewer case for every consumer.
            for offset in range(i2 - i1):
                out.append(DiffLine(LineOp.REMOVE, old_lines[i1 + offset], i1 + offset + 1, None))
            for offset in range(j2 - j1):
                out.append(DiffLine(LineOp.ADD, new_lines[j1 + offset], None, j1 + offset + 1))
    return tuple(out)


def changed_line_count(diff: tuple[DiffLine, ...]) -> int:
    return sum(1 for line in diff if line.op is not LineOp.KEEP)
