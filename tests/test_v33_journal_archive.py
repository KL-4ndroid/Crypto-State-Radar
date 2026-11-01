import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.research_journal import ResearchJournal
from src.v33.journal_archive import JournalArchive, KIND, MAX_TOTAL_BYTES


def test_restore_dedup_quota_and_live_revision(tmp_path):
    journal = ResearchJournal(tmp_path/'archive.db')
    archive = JournalArchive(journal, max_objects=1)
    original = journal.load()
    body = b'{"measurements":[1,2,3]}'
    row = archive.put(body)
    assert archive.read(row['sha256']) == body
    assert row == archive.put(body)
    assert archive.stats()['objects'] == 1
    assert 'body_base64' not in row
    with pytest.raises(OSError, match='quota'):
        archive.put(b'another')
    assert journal.load() == original
    journal.close()
    reopened = ResearchJournal(tmp_path/'archive.db')
    assert JournalArchive(reopened).read(row['sha256']) == body
    reopened.close()


def test_logical_byte_budget_and_bounded_decompression(tmp_path):
    journal = ResearchJournal(tmp_path/'archive.db')
    archive = JournalArchive(journal, max_total_bytes=20)
    with pytest.raises(OSError):
        archive.put(b'a')
    assert archive.stats()['objects'] == 0
    archive = JournalArchive(journal, max_object_bytes=8)
    with pytest.raises(ValueError):
        archive.put(b'a'*9)
    row = archive.put(b'a'*8)
    record = journal.record(KIND, row['sha256'])
    record['uncompressed_bytes'] = 7
    with pytest.raises(ValueError):
        archive._decode(row['sha256'], record)
    with pytest.raises(FileNotFoundError):
        archive.read('a'*64)
    with pytest.raises(ValueError):
        archive.read('../no')
    journal.close()


def test_concurrent_workers_share_cap(tmp_path):
    path = tmp_path/'archive.db'
    ResearchJournal(path).close()
    def upload(index):
        journal = ResearchJournal(path)
        try:
            archive = JournalArchive(journal, max_objects=1)
            try:
                archive.put(str(index).encode())
                return 'stored'
            except OSError:
                return 'full'
        finally:
            journal.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(upload, (1, 2))) == ['full', 'stored']


def test_no_quota_escalation_or_ephemeral_cloud(tmp_path, monkeypatch):
    journal = ResearchJournal(tmp_path/'archive.db')
    with pytest.raises(ValueError):
        JournalArchive(journal, max_total_bytes=MAX_TOTAL_BYTES+1)
    monkeypatch.setenv('VERCEL', '1')
    with pytest.raises(RuntimeError):
        JournalArchive(journal)
    journal.close()
