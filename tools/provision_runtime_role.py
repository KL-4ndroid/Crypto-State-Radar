"""Explicit maintenance operation; credentials enter and leave through private files.

Run after research_worker migrate, using a direct administrator connection.
Never deploy the administrator URL as CSR_RESEARCH_DATABASE_URL.
"""
import argparse
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

import psycopg
from psycopg import sql


def provision(admin_url, pooled_url, role, environment):
    password = secrets.token_urlsafe(40)
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=15) as connection:
        with connection.transaction():
            label = connection.execute("SELECT environment FROM csr_runtime WHERE id=1 FOR UPDATE").fetchone()[0]
            if label != environment:
                raise ValueError("ledger environment mismatch")
            # Refuse rotation or modification of an existing role implicitly.
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                raise ValueError("runtime role already exists")
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role), sql.Literal(password)))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            for privileges, table in (("SELECT,INSERT", "csr_records"), ("SELECT", "csr_runtime"), ("SELECT,INSERT,UPDATE", "csr_validation_jobs"), ("SELECT,INSERT,UPDATE", "csr_scheduler_wakes")):
                connection.execute(sql.SQL("GRANT " + privileges + " ON {} TO {}").format(sql.Identifier(table), sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT UPDATE(revision,payload) ON csr_runtime TO {}").format(sql.Identifier(role)))
            # Modern PostgreSQL defaults already deny this; explicitly protect older databases.
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    url = urlsplit(pooled_url)
    authority = quote(role, safe="") + ":" + quote(password, safe="") + "@" + url.hostname
    if url.port:
        authority += ":" + str(url.port)
    return urlunsplit((url.scheme, authority, url.path, url.query, url.fragment))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", required=True, help="private JSON containing admin_url and pooled_url")
    parser.add_argument("--output", required=True, help="new private JSON file; never overwrite")
    parser.add_argument("--role", required=True)
    parser.add_argument("--environment", choices=["preview", "production", "test"], required=True)
    args = parser.parse_args()
    try:
        destination = Path(args.output)
        # Reserve output before changing the database so reruns cannot lose credentials.
        with destination.open("x", encoding="utf-8") as output:
            credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
            url = provision(credentials["admin_url"], credentials["pooled_url"], args.role, args.environment)
            json.dump({"CSR_RESEARCH_DATABASE_URL": url, "CSR_RESEARCH_ENVIRONMENT": args.environment}, output)
        print("runtime role provisioned; credentials saved privately")
    except Exception as exc:
        print("runtime role provisioning failed: " + type(exc).__name__)
        raise SystemExit(1)
