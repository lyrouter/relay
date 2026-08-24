"""Write the **whole** OpenAPI document for the frontend's type generation.

    uv run python scripts/dump_web_schema.py
    cd web && npm run types

Separate from ``scripts/gen_openapi.py`` on purpose, because the two answer
different questions:

* ``gen_openapi.py`` produces the committed ``/api/v1`` **snapshot**. It is a
  gate: a contract change must be updated by a human in the PR (API-5). ``/web``
  is deliberately excluded — it ships with its consumer, and a gate that fires on
  routine UI work is one people learn to regenerate without reading;
* this writes the full document, ``/web`` included, purely as codegen input. It is
  **not** committed (``web/.gitignore``): it is derived, and a stale copy in the
  repository would be a second source of truth for the shapes the UI compiles
  against.

That is §8.9's last clause — the frontend's types come from the same schema, so a
field the backend renames breaks the frontend **build** rather than a page.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from relay.api.app import create_app

OUTPUT = Path(__file__).resolve().parents[1] / "web" / "src" / "api" / "schema.json"


def main() -> int:
    document = create_app().openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT}: {len(document['paths'])} paths")
    print("next: cd web && npm run types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
