"""Export accumulated research into a portable, ChatGPT-friendly bundle.

No ChatGPT/OpenAI API is called. The user can download the generated files and
provide them to ChatGPT manually for V2.x research and rule refinement.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from store_factory import create_store

TABLES = ("signals", "validations", "transitions", "observations")


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple)):
            out[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            out[key] = value
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    flat = [_flatten(x) for x in rows]
    keys = sorted({k for row in flat for k in row})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(flat)


def export_bundle(output_root: str | Path = "exports") -> Path:
    store = create_store()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_root) / f"crypto-state-radar-{stamp}"
    root.mkdir(parents=True, exist_ok=False)
    counts: dict[str, int] = {}

    for table in TABLES:
        rows = list(store.read(table))
        counts[table] = len(rows)
        _write_csv(root / f"{table}.csv", rows)

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 1,
        "counts": counts,
        "notes": [
            "Signals preserve only signal-time knowledge and must be treated as immutable.",
            "Validations contain future outcomes and must not be joined into signal-time features before evaluation.",
            "No ChatGPT/OpenAI API was used to create this dataset."
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


if __name__ == "__main__":
    print(export_bundle())
