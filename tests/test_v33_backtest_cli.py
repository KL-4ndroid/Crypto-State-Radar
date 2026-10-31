import json
from pathlib import Path
import subprocess
import sys

from test_v33_backtest import fixture, at

ROOT = Path(__file__).resolve().parents[1]


def test_offline_replay_is_stable_and_preserves_evidence(tmp_path):
    signal, bars, policy = fixture()
    document = dict(policy=policy.__dict__, observed_until=at(20),
                    cases=[dict(signal=signal, bars=bars, funding=[], cluster='2026-01-01/down')])
    source = tmp_path/'cases.json'
    source.write_text(json.dumps(document), encoding='utf-8')
    outputs = [tmp_path/'first.json', tmp_path/'second.json']
    command = [sys.executable, str(ROOT/'tools/v33_backtest.py'), '--input', str(source), '--output']
    for output in outputs:
        result = subprocess.run([*command, str(output)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    report = json.loads(outputs[0].read_text())
    assert report['binance_requests'] == report['production_writes'] == 0
    assert report['metrics']['raw_n'] == 1
    assert report['metrics']['ci95'] is None
    before = source.read_bytes(), outputs[0].read_bytes()
    for output in (source, outputs[0]):
        assert subprocess.run([*command, str(output)], capture_output=True).returncode != 0
    assert before == (source.read_bytes(), outputs[0].read_bytes())
