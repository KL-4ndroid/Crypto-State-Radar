"""Exercise CLI orchestration, not just the collector's domain functions."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys


def test_watch_recovers_publication_then_collects_and_releases_lock(monkeypatch,tmp_path,capsys):
    path=Path(__file__).resolve().parents[1]/'tools/bsc_radar.py'
    spec=importlib.util.spec_from_file_location('bsc_worker_cli_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    journal=SimpleNamespace(close=lambda:calls.append('close'))
    monkeypatch.setattr(module,'open_journal',lambda root:journal)
    monkeypatch.setattr(module,'stream_for',lambda journal:SimpleNamespace(load=lambda:(1,{})))
    monkeypatch.setattr(module,'publish_current',lambda journal:calls.append('recover'))
    def collect(journal,**kwargs):
        assert kwargs['publish'] is True
        calls.append('collect')
        return {'lag_blocks':0,'execution_enabled':False}
    monkeypatch.setattr(module,'run',collect)
    monkeypatch.setattr(sys,'argv',[str(path),'watch','--root',str(tmp_path),'--cycles','1','--publish'])
    module.main()
    assert calls==['recover','collect','close']
    report=json.loads(capsys.readouterr().out)
    assert report['cycle']==report['max_cycles']==1 and report['execution_enabled'] is False
    # The worker must not leave its single-instance lock held after completion.
    with sqlite3.connect(tmp_path/'worker-lock.sqlite3',timeout=.1) as db:
        db.execute('BEGIN EXCLUSIVE')
