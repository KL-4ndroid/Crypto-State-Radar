"""Inspect available algorithms or verify a selection; no network or writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.algorithms.registry import resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['list', 'check'])
    parser.add_argument('--algorithm', default='distribution-v33')
    parser.add_argument('--rule', type=Path)
    parser.add_argument('--target', choices=['research', 'live'], default='research')
    args = parser.parse_args()
    try:
        if args.command == 'list':
            rows = [dict(resolve(name).identity.row(), live_enabled=name == 'radar-v2')
                    for name in ('radar-v2', 'distribution-v33')]
        else:
            rule = json.loads(args.rule.read_text(encoding='utf-8')) if args.rule else None
            rows = resolve(args.algorithm, rule=rule, target=args.target).identity.row()
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
