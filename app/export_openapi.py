"""Writes openapi/openapi.json from the app's own routes and models (no database needed).

    python -m app.export_openapi

A test fails when the committed file is stale; ecommerce-contracts syncs this file.
"""

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

OUT = Path(__file__).resolve().parent.parent / "openapi" / "openapi.json"


def build_document() -> dict:
    # Placeholder settings: the document depends only on routes and models.
    settings = Settings(database_url="postgresql+psycopg://unused:unused@localhost:5432/unused")
    return create_app(settings).openapi()


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(build_document(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
