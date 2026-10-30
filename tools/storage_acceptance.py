"""Create disposable loopback services, verify real adapters, then remove them.

Run with a Python environment containing pytest and psycopg[binary]. Docker
Desktop must be running. No existing database URL or Redis credentials are used.
"""
import json
import argparse
import os
import re
import subprocess
import sys
import time
from uuid import uuid4
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POSTGRES="postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b"
REDIS="redis:7-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f"


def command(*args,check=True):
    result=subprocess.run(args,cwd=ROOT,text=True,encoding="utf-8",capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-only',action='store_true')
    parser.add_argument('--test-filter',help='pytest -k expression for the changed storage slice')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/verification/storage-services.json')
    args=parser.parse_args()
    suffix=uuid4().hex[:8]
    names={"postgres":"csr-acceptance-pg-"+suffix,"redis":"csr-acceptance-redis-"+suffix}
    report={"isolated":True,"production_writes":False,"images":{"postgres":POSTGRES,**({} if args.postgres_only else {'redis':REDIS})}}
    owned=[]
    try:
        services=[(names["postgres"],POSTGRES,"5432",["-e","POSTGRES_PASSWORD=local-acceptance-only","-e","POSTGRES_DB=radar_acceptance"])]
        if not args.postgres_only:services.append((names["redis"],REDIS,"6379",[]))
        for name,image,port,extra in services:
            owned.append(name)
            command("docker","run","--detach","--rm","--name",name,"--label","csr.acceptance=true","-p",f"127.0.0.1::{port}",*extra,image)
        deadline=time.monotonic()+40
        probes=[(names["postgres"],["pg_isready","-U","postgres"])]
        if not args.postgres_only:probes.append((names["redis"],["redis-cli","PING"]))
        for name,probe_args in probes:
            while command("docker","exec",name,*probe_args,check=False).returncode:
                if time.monotonic()>deadline:raise RuntimeError("local service readiness timed out")
                time.sleep(0.25)
        ports={}
        for kind,port in (("postgres","5432"),("redis","6379")):
            if args.postgres_only and kind=='redis':continue
            address=command("docker","port",names[kind],port).stdout.strip()
            match=re.fullmatch(r"127\.0\.0\.1:(\d+)",address)
            if not match:raise RuntimeError("acceptance service must bind loopback only")
            ports[kind]=match[1]
        environment=dict(os.environ,CSR_LOCAL_STORAGE_ACCEPTANCE="1",CSR_TEST_PG_PORT=ports["postgres"],PYTHONDONTWRITEBYTECODE="1")
        if 'redis' in ports:environment['CSR_TEST_REDIS_PORT']=ports['redis']
        test_args=[sys.executable,"-m","pytest","tests/test_storage_services.py","-q","-p","no:cacheprovider"]
        if args.test_filter:test_args.extend(['-k',args.test_filter])
        result=subprocess.run(test_args,cwd=ROOT,env=environment,text=True,encoding="utf-8",capture_output=True)
        report.update(exit_code=result.returncode,pytest=result.stdout,stderr=result.stderr,
            postgres=command("docker","exec",names["postgres"],"psql","-U","postgres","-d","radar_acceptance","-tAc","show server_version").stdout.strip(),
            redis=None if args.postgres_only else command("docker","exec",names["redis"],"redis-server","--version").stdout.strip(),
            test_filter=args.test_filter,postgres_only=args.postgres_only)
        print(result.stdout,flush=True)
        if result.returncode:raise RuntimeError("storage acceptance failed")
    finally:
        cleanup={}
        for name in owned:
            label=command("docker","inspect","--format",'{{index .Config.Labels "csr.acceptance"}}',name,check=False)
            if label.returncode==0 and label.stdout.strip()=="true":
                cleanup[name]=command("docker","stop",name,check=False).returncode==0
        report["cleanup"]=cleanup
        out=args.output
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(str(out),flush=True)


if __name__=="__main__":main()
