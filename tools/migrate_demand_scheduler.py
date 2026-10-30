"""Explicit additive migration; never deploy administrator credentials."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql


def migrate(credentials, runtime, environment):
    if runtime.get('CSR_RESEARCH_ENVIRONMENT') != environment:
        raise ValueError('runtime environment mismatch')
    admin=urlsplit(credentials['admin_url'])
    target=urlsplit(runtime['CSR_RESEARCH_DATABASE_URL'])
    if (admin.hostname.replace('-pooler',''),admin.path) != (target.hostname.replace('-pooler',''),target.path):
        raise ValueError('migration database mismatch')
    with psycopg.connect(credentials['admin_url'],autocommit=True,connect_timeout=8) as connection:
        with connection.transaction():
            connection.execute('SET LOCAL statement_timeout=10000')
            connection.execute('SET LOCAL lock_timeout=2000')
            label=connection.execute('SELECT environment FROM csr_runtime WHERE id=1 FOR UPDATE').fetchone()[0]
            if label!=environment:
                raise ValueError('ledger environment mismatch')
            role=connection.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s',(target.username,)).fetchone()
            if not role or any(role):
                raise ValueError('expected an existing restricted runtime role')
            connection.execute((Path(__file__).resolve().parents[1]/'schema/demand_scheduler.sql').read_text(encoding='utf-8'))
            connection.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON csr_scheduler_wakes TO {}').format(sql.Identifier(target.username)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials',required=True)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--environment',choices=['preview','production'],required=True)
    args=parser.parse_args()
    try:
        migrate(json.loads(Path(args.credentials).read_text(encoding='utf-8')),
                json.loads(Path(args.runtime).read_text(encoding='utf-8')),args.environment)
        print('wake schema and restricted runtime grants applied: '+args.environment)
    except Exception as exc:
        raise SystemExit('wake migration failed: '+type(exc).__name__) from None
