"""Write the OpenAPI schema to console/openapi.json.

The console ships a checked-in spec that predates this backend. Regenerating it
here keeps the documented surface honest, even though the screens call the API
through `src/api/client.ts` rather than a generated client.

    python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app  # noqa: E402

TARGETS = [
    Path(__file__).resolve().parent.parent / "openapi.json",
    Path(__file__).resolve().parents[2] / "console" / "openapi.json",
]


def main() -> None:
    schema = create_app().openapi()
    blob = json.dumps(schema, indent=2) + "\n"
    for target in TARGETS:
        if target.parent.exists():
            target.write_text(blob)
            print(f"wrote {target} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
