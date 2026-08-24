"""API-5 · the OpenAPI snapshot gate for ``/api/v1`` (design §8.6).

    uv run python scripts/gen_openapi.py          # write
    uv run python scripts/gen_openapi.py --check  # diff only, non-zero on drift

**The direction of truth is inverted by the stack, and the discipline has to
follow.** The original plan was "the spec is the source of truth; fail CI when the
implementation disagrees". With FastAPI the spec is *generated from* the
implementation, so that check is vacuously true — a gate that can never fire,
while a breaking change sails through. So this is a **snapshot** instead: the
generated document is committed, CI regenerates and diffs it, and any difference
fails until a human updates the snapshot inside the PR.

The point is not to block change. It is to make every contract change **appear in
a diff a reviewer reads**. A deleted field, a changed type or a changed enum
meaning is then visible as what it is: a v2-level change (§8.6 rule 2), not a
refactor.

**Only ``/api/v1`` is in the snapshot** (§8.9). ``/web`` ships with the frontend
that consumes it and is versionless — putting it here would mean every UI tweak
demands a snapshot update, which is how a gate becomes a formality people learn to
regenerate without reading. Components are filtered to those the v1 paths actually
reach, transitively, for the same reason: a schema that only ``/web`` uses must not
make v1's snapshot churn.

Frontend TS types come from this same file, so a mismatch breaks the frontend
build rather than production.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from relay.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "openapi.json"

PREFIX = "/api/v1"

#: A ``$ref`` looks like ``#/components/schemas/TicketResponse``. Matched with a
#: regex over the serialised subtree rather than by walking types, because a ref
#: can appear anywhere a schema can and a walker that misses one silently drops a
#: component the contract depends on.
REF = re.compile(r'"#/components/([^/"]+)/([^"]+)"')


def snapshot() -> dict[str, Any]:
    """The v1 slice of the generated document, in a stable serialisation."""
    document = create_app().openapi()
    paths = {
        path: spec
        for path, spec in sorted(document.get("paths", {}).items())
        if path.startswith(PREFIX)
    }
    components = _reachable_components(paths, document.get("components", {}))
    return {
        "openapi": document.get("openapi"),
        "info": {
            "title": document.get("info", {}).get("title"),
            # Deliberately **not** the application version: bumping the app would
            # otherwise dirty the contract snapshot, which teaches people to
            # regenerate it without reading the diff. v1 is the contract's version.
            "version": "v1",
        },
        "paths": paths,
        "components": components,
    }


def _reachable_components(paths: dict, components: dict) -> dict:
    """Every component the v1 paths reach, transitively.

    Iterating to a fixed point rather than one pass: a response schema refers to
    a payload schema which refers to an enum, and stopping early would produce a
    snapshot with dangling ``$ref``s — valid-looking and unusable for codegen.
    """
    wanted: set[tuple[str, str]] = set(_refs_in(paths))
    while True:
        found = set(wanted)
        for section, name in wanted:
            definition = components.get(section, {}).get(name)
            if definition is not None:
                found |= set(_refs_in(definition))
        if found == wanted:
            break
        wanted = found

    result: dict[str, dict] = {}
    for section, name in sorted(wanted):
        definition = components.get(section, {}).get(name)
        if definition is not None:
            result.setdefault(section, {})[name] = definition
    return result


def _refs_in(subtree: Any) -> list[tuple[str, str]]:
    return [
        (section, name)
        for section, name in REF.findall(json.dumps(subtree, sort_keys=True))
    ]


def rendered() -> str:
    return json.dumps(snapshot(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or check the /api/v1 snapshot.")
    parser.add_argument("--check", action="store_true", help="diff only; do not write")
    args = parser.parse_args()

    current = rendered()
    if not args.check:
        OUTPUT.write_text(current, encoding="utf-8")
        document = json.loads(current)
        print(f"wrote {OUTPUT.relative_to(ROOT)}: {len(document['paths'])} paths")
        return 0

    if not OUTPUT.exists():
        print(
            f"{OUTPUT.relative_to(ROOT)} is missing. Run: "
            "uv run python scripts/gen_openapi.py",
            file=sys.stderr,
        )
        return 1

    committed = OUTPUT.read_text(encoding="utf-8")
    if committed == current:
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0

    print(
        "The /api/v1 contract changed but the committed snapshot did not.\n"
        "\n"
        "Read the diff below before regenerating it. If it removes a field,\n"
        "changes a type, or changes what an enum value means, that is a **v2**\n"
        "change (design §8.6) and must not be merged into v1 — additive change\n"
        "only. If it is additive, regenerate and commit:\n"
        "\n"
        "    uv run python scripts/gen_openapi.py\n",
        file=sys.stderr,
    )
    for line in difflib.unified_diff(
        committed.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile="openapi.json (committed)",
        tofile="openapi.json (generated)",
    ):
        sys.stderr.write(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
