"""Export immutable research journal into portable CSV/JSON files.

The exported bundle is designed to be downloaded and uploaded to ChatGPT for
offline research. The live system itself never calls the ChatGPT/OpenAI API.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_bundle(journal_dir: str = "data/journal", output_root: str = "data/exports") -> Path:
    src = Path(journal_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(output_root) / stamp
    out.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"created_at": datetime.now(timezone.utc).isoformat(), "format_version": 1, "datasets": {}}
    for name in ("observations", "signals", "transitions", "validations"):
        rows = _read_jsonl(src / f"{name}.jsonl")
        _write_csv(out / f"{name}.csv", rows)
        manifest["datasets"][name] = {"rows": len(rows), "file": f"{name}.csv"}

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(export_bundle())
