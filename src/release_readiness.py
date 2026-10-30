"""Read-only configuration and PostgreSQL privilege checks before deployment."""
import os
import json
from urllib.parse import urlparse


def configuration_checks(environment,stage="preview"):
    scan=environment.get("CSR_SCAN_TOKEN","")
    worker=environment.get("CSR_WORKER_TOKEN","")
    key=environment.get("CSR_STATE_KEY","")
    url=urlparse(environment.get("CSR_RESEARCH_WORKER_URL",""))
    return {
        "database_configured":bool(environment.get("CSR_RESEARCH_DATABASE_URL")),
        "database_environment_matches":environment.get("CSR_RESEARCH_ENVIRONMENT")==stage,
        "scan_token_configured":bool(scan),
        "separate_worker_token":bool(worker) and worker!=scan,
        "state_store_configured":bool(environment.get("KV_REST_API_URL") and environment.get("KV_REST_API_TOKEN")),
        "preview_state_key_isolated":stage!="preview" or bool(key and key!="crypto-state-radar:state:v1"),
        "scan_mode_explicit":environment.get("CSR_SCAN_MODE") in {"live","shadow"},
        "worker_url_https":url.scheme=="https" and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment,
    }


def inspect(stage="preview",connections=False):
    checks=configuration_checks(os.environ,stage)
    report={"stage":stage,"checks":checks,"configuration_ready":all(checks.values()),"connection_checks":"not_requested",
        "deploys_or_migrates":False,"production_ready":False}
    if connections and checks["database_configured"] and checks["database_environment_matches"]:
        try:
            import psycopg
            with psycopg.connect(os.environ["CSR_RESEARCH_DATABASE_URL"],autocommit=True,connect_timeout=8) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL statement_timeout=5000")
                    revision,ledger_id,label=connection.execute("SELECT revision,ledger_id,environment FROM csr_runtime WHERE id=1").fetchone()
                    insert,update,delete,ddl=connection.execute("SELECT has_table_privilege(current_user,'csr_records','INSERT'),has_table_privilege(current_user,'csr_records','UPDATE'),has_table_privilege(current_user,'csr_records','DELETE'),has_schema_privilege(current_schema(),'CREATE')").fetchone()
                    trigger=connection.execute("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='csr_records'::regclass AND tgname='csr_immutable_record' AND tgenabled<>'D')").fetchone()[0]
                    retry_columns=connection.execute("SELECT next_attempt_at,lease_until,lease_token FROM csr_validation_jobs LIMIT 0").description
                    protected=not connection.execute("SELECT has_column_privilege(current_user,'csr_runtime','ledger_id','UPDATE') OR has_column_privilege(current_user,'csr_runtime','environment','UPDATE')").fetchone()[0]
                    report["connection_checks"]={"ledger_readable":revision>=0 and bool(ledger_id),"environment_bound":label==stage,"ledger_identity_protected":protected,"immutable_records_privileges":insert and not update and not delete,
                        "runtime_has_no_schema_ddl":not ddl,"immutability_trigger":bool(trigger),"retry_schema":len(retry_columns)==3}
        except Exception as exc:
            # Never include exception messages, DSNs, tokens or environment values.
            report["connection_checks"]={"error_type":type(exc).__name__}
    report["remaining_gates"]=["Vercel sin1 preview throughput","isolated live/shadow comparison","representative historical event evaluation","reviewed production switch and rollback"]
    return report


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--stage",choices=["preview","production"],default="preview")
    parser.add_argument("--connections",action="store_true")
    args=parser.parse_args();report=inspect(args.stage,args.connections)
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["configuration_ready"] and (not args.connections or isinstance(report["connection_checks"],dict) and all(v is True for v in report["connection_checks"].values())) else 2)
