#!/usr/bin/env python3
"""Generate the Automation Blueprints docs/catalog index.

The website build imports this module to serialize the runtime blueprint catalog
into a flat JSON-compatible list.  Keeping the logic here (instead of duplicating
it in docs) lets tests compare the docs surface against cron.blueprint_catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cron.blueprint_catalog import CATALOG, blueprint_catalog_entry  # noqa: E402


def build_index() -> list[dict[str, Any]]:
    """Return the serializable automation blueprint catalog index."""
    return [blueprint_catalog_entry(blueprint) for blueprint in CATALOG]


def main() -> None:
    json.dump(build_index(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
