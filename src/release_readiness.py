"""Read-only configuration and PostgreSQL privilege checks before deployment."""
import os
import json
from urllib.parse import urlparse


def https_origin(value):
    url=urlparse(value or "")
    return url.scheme=="https" and bool(url.hostname) and not url.username and not url.password and url.path in {"","/"} and not url.query and not url.fragment


def configuration_checks(environment,stage="preview"):
    scan=environment.get("CSR_SCAN_TOKEN","")
    worker=environment.get("CSR_WORKER_TOKEN","")
    key=environment.get("CSR_STATE_KEY","")
    url=urlparse(environment.get("CSR_RESEARCH_WORKER_URL",""))
    checks = {
        "scheduler_mode_valid":environment.get("CSR_SCHEDULER_MODE","periodic") in {"periodic","demand"},
        "database_configured":bool(environment.get("CSR_RESEARCH_DATABASE_URL")),
        "database_environment_matches":environment.get("CSR_RESEARCH_ENVIRONMENT")==stage,
        "scan_token_configured":bool(scan),
        "separate_worker_token":bool(worker) and worker!=scan,
        "state_store_configured":bool(environment.get("KV_REST_API_URL") and environment.get("KV_REST_API_TOKEN")),
        "preview_state_key_isolated":stage!="preview" or bool(key and key!="crypto-state-radar:state:v1"),
        "scan_mode_explicit":environment.get("CSR_SCAN_MODE") in {"live","shadow"},
    }
    if environment.get("CSR_SCHEDULER_ENABLED") is not None or environment.get("QSTASH_CURRENT_SIGNING_KEY"):
        current=environment.get("QSTASH_CURRENT_SIGNING_KEY","")
        next_key=environment.get("QSTASH_NEXT_SIGNING_KEY","")
        origin=environment.get("CSR_SCHEDULER_ORIGIN") or ("https://"+environment["VERCEL_URL"] if environment.get("VERCEL_URL") else "")
        checks.update(scheduler_enabled=environment.get("CSR_SCHEDULER_ENABLED")=="true",
            qstash_signing_keys_configured=bool(current and next_key and current!=next_key and "[SENSITIVE]" not in current+next_key),
            scheduler_origin_https=https_origin(origin),
            production_scheduler_origin_matches=stage!="production" or origin.rstrip("/")=="https://crypto-state-radar.vercel.app")
        if environment.get('CSR_SCHEDULER_MODE')=='demand':
            provider=urlparse(environment.get('QSTASH_URL',''))
            checks['qstash_publisher_configured']=bool(environment.get('QSTASH_TOKEN') and provider.scheme=='https' and provider.hostname and provider.hostname.endswith('.upstash.io'))
    else:
        checks["worker_url_https"]=url.scheme=="https" and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment
    return checks


def inspect(stage="preview",connections=False):
    checks=configuration_checks(os.environ,stage)
    report={"stage":stage,"checks":checks,"configuration_ready":all(checks.values()),"connection_checks":"not_requested",
        "deploys_or_migrates":False,"production_ready":None,
        "assessment_scope":"configuration_and_database_privileges_only",
        "scheduler_source":"qstash" if "scheduler_enabled" in checks else "manual_or_legacy_worker",
        "operational_acceptance":"not_assessed","model_effectiveness":"not_assessed"}
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
                    if os.getenv('CSR_SCHEDULER_MODE')=='demand':
                        connection.execute('SELECT operation,token,due_at,status,lease_until FROM csr_scheduler_wakes LIMIT 0')
                        privileges=connection.execute("SELECT has_table_privilege(current_user,'csr_scheduler_wakes','SELECT') AND has_table_privilege(current_user,'csr_scheduler_wakes','INSERT') AND has_table_privilege(current_user,'csr_scheduler_wakes','UPDATE'),has_table_privilege(current_user,'csr_scheduler_wakes','DELETE')").fetchone()
                        report['connection_checks']['wake_outbox_privileges']=bool(privileges[0] and not privileges[1])
        except Exception as exc:
            # Never include exception messages, DSNs, tokens or environment values.
            report["connection_checks"]={"error_type":type(exc).__name__}
    report["not_assessed_by_this_tool"]=["24-hour operational acceptance","representative historical and mature forward evaluation","recorded restore and rollback drill"]
    return report


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--stage",choices=["preview","production"],default="preview")
    parser.add_argument("--connections",action="store_true")
    args=parser.parse_args();report=inspect(args.stage,args.connections)
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["configuration_ready"] and (not args.connections or isinstance(report["connection_checks"],dict) and all(v is True for v in report["connection_checks"].values())) else 2)
