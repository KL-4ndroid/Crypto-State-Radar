"""Small, quota-bounded raw archive on the existing durable research journal.

Not a replacement for a large object store. A global kind and PG advisory lock
coordinate the logical payload budget across runs and cooperating workers.
Never deletes objects or increases quotas automatically.
"""
import base64
import gzip
import hashlib
import io
import os
import re

from .contracts import encode

KIND = 'v33:raw:archive:gzip-1'
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_OBJECT_BYTES = 256 * 1024
MAX_OBJECTS = 10000
LOCK_ID = 833003301


class JournalArchive:
    durability = 'JOURNAL_PERSISTENT'

    def __init__(self, journal, max_total_bytes=MAX_TOTAL_BYTES,
                 max_object_bytes=MAX_OBJECT_BYTES, max_objects=MAX_OBJECTS):
        for value, ceiling in ((max_total_bytes, MAX_TOTAL_BYTES),
                               (max_object_bytes, MAX_OBJECT_BYTES), (max_objects, MAX_OBJECTS)):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError('archive quota exceeds bounded policy')
        if os.getenv('VERCEL') and not journal.pg:
            raise RuntimeError('ephemeral SQLite is not a durable cloud archive')
        self.journal = journal
        self.max_total_bytes = max_total_bytes
        self.max_object_bytes = max_object_bytes
        self.max_objects = max_objects

    def _key(self, key):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
            raise ValueError('invalid archive hash')

    def _usage(self):
        count, size = self.journal.sql(
            'SELECT COUNT(*),COALESCE(SUM(LENGTH(payload)),0) FROM csr_records WHERE kind=?',
            (KIND,)).fetchone()
        return count, size

    def put(self, body):
        if not isinstance(body, bytes) or len(body) > self.max_object_bytes:
            raise ValueError('raw response exceeds journal object budget')
        key = hashlib.sha256(body).hexdigest()
        packed = gzip.compress(body, mtime=0)
        payload = dict(sha256=key, compressed_bytes=len(packed), uncompressed_bytes=len(body),
                       backend=self.durability, archive_version='gzip-json-1',
                       body_base64=base64.b64encode(packed).decode('ascii'))
        with self.journal.transaction():
            if self.journal.pg:
                self.journal.sql('SELECT pg_advisory_xact_lock(?)', (LOCK_ID,))
            old = self.journal.record(KIND, key)
            if old is None:
                count, size = self._usage()
                if count >= self.max_objects or size + len(encode(payload)) > self.max_total_bytes:
                    raise OSError('journal raw archive quota exhausted')
                self.journal._insert(KIND, key, payload)
            else:
                # Verify existing bytes instead of silently accepting a hash key.
                self._decode(key, old)
                payload = old
        return {k: v for k, v in payload.items() if k != 'body_base64'}

    def _decode(self, key, row):
        try:
            packed = base64.b64decode(row['body_base64'], validate=True)
            if len(packed) != row['compressed_bytes']:
                raise ValueError('invalid compressed size')
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as source:
                body = source.read(self.max_object_bytes + 1)
            if (len(body) > self.max_object_bytes or len(body) != row['uncompressed_bytes']
                    or hashlib.sha256(body).hexdigest() != key or row['sha256'] != key):
                raise ValueError('archive checksum mismatch')
            return body
        except (KeyError, TypeError, OSError, EOFError) as exc:
            raise ValueError('invalid journal raw object') from exc

    def read(self, key):
        self._key(key)
        row = self.journal.record(KIND, key)
        if row is None:
            raise FileNotFoundError('journal raw object missing')
        return self._decode(key, row)

    def stats(self):
        count, size = self._usage()
        return dict(backend=self.durability, objects=count, logical_payload_bytes=size,
                    max_total_bytes=self.max_total_bytes, max_objects=self.max_objects,
                    max_object_bytes=self.max_object_bytes, quota_scope='GLOBAL_ARCHIVE_KIND',
                    physical_database_bytes='NOT_MEASURED', retention='APPEND_ONLY_STOP_AT_CAP')
