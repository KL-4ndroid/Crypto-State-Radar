"""Offline V3.3 execution replay from explicit JSON cases; never acquires data."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.v33.backtest import ExecutionPolicy, simulate_short, summarize, purged_split
from src.v33.contracts import encode, digest


def replay(document):
    policy = ExecutionPolicy(**document['policy'])
    results, clusters = [], {}
    for case in document['cases']:
        signal = case['signal']
        if signal['signal_id'] in clusters:
            raise ValueError('duplicate signal identity')
        clusters[signal['signal_id']] = case['cluster']
        results.append(simulate_short(signal, case['bars'], case['funding'],
                                      document['observed_until'], policy))
    report = dict(schema_version=1, input_hash=digest(document),
                  model_status='NOT_VALIDATED', policy=policy.__dict__,
                  results=results, metrics=summarize(results, clusters),
                  binance_requests=0, production_writes=0)
    if 'split' in document:
        report['partitions'] = purged_split(document['samples'], **document['split'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('output must not overwrite input')
    # Immutable output: prevent accidental replacement of any journal or evidence.
    if args.output.exists():
        parser.error('output already exists; choose a new evidence path')
    report = replay(json.loads(args.input.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=args.output.parent, mode='w',
                                         encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encode(report) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Hard link atomically creates only if absent; never overwrites evidence.
        os.link(temporary, args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(encode(dict(output=str(args.output), input_hash=report['input_hash'],
                      model_status=report['model_status'], metrics=report['metrics'])))


if __name__ == '__main__':
    main()
