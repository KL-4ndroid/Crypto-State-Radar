"""Operate only this project's named QStash schedules; never print credentials."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import urlparse

from qstash import QStash

CRONS = {"full_scan":"7 * * * *", "drain":"3-58/5 * * * *", "validate":"4-59/5 * * * *"}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['plan','prepare','activate','pause','status','probe'])
    p.add_argument('--environment',choices=['preview','production'],required=True)
    p.add_argument('--origin',required=True)
    p.add_argument('--credentials',required=True,help='Ignored local JSON file with QSTASH_TOKEN and QSTASH_URL')
    p.add_argument('--preview-auth',help='Ignored Vercel env file with a short-lived OIDC token')
    p.add_argument('--operation',choices=list(CRONS),default='full_scan')
    args=p.parse_args()
    origin=args.origin.rstrip('/')
    url=urlparse(origin)
    if url.scheme!='https' or url.path or url.query or url.fragment or url.username:
        p.error('expected an HTTPS origin')
    if args.environment=='production' and origin!='https://crypto-state-radar.vercel.app':
        p.error('production target must be the existing project domain')
    if args.environment=='preview' and (not url.hostname.startswith('crypto-state-radar-') or not url.hostname.endswith('.vercel.app')):
        p.error('preview must target this project on vercel.app')
    queue='csr-'+args.environment+'-v1'
    ids={op:queue+'-'+op.replace('_','-') for op in CRONS}
    target=origin+'/api/scheduler'
    expected=[dict(operation=op,schedule_id=ids[op],cron=cron,destination=target,flow_key=queue) for op,cron in CRONS.items()]
    if args.command=='plan':
        print(json.dumps(dict(environment=args.environment,deliveries_per_day=600,retries=2,parallelism=1,schedules=expected),indent=2));return
    secrets=json.loads(Path(args.credentials).read_text(encoding='utf-8'))
    provider=urlparse(secrets['QSTASH_URL'])
    if provider.scheme!='https' or not provider.hostname.endswith('.upstash.io'):
        p.error('unexpected QStash API origin')
    client=QStash(secrets['QSTASH_TOKEN'],base_url=secrets['QSTASH_URL'],retry=False)
    headers={}
    if args.preview_auth:
        if args.environment!='preview':p.error('preview authentication is never forwarded to production')
        for line in Path(args.preview_auth).read_text(encoding='utf-8').splitlines():
            if line.startswith('VERCEL_OIDC_TOKEN='):
                raw=line.partition('=')[2]
                headers['x-vercel-trusted-oidc-idp-token']=json.loads(raw) if raw.startswith('"') else raw
        if not headers:p.error('preview authentication token is missing')
    if args.command=='prepare':
        for row in expected:
            client.schedule.create(destination=target,cron=row['cron'],flow_control={'key':queue,'parallelism':1},
                body=json.dumps({'operation':row['operation']}),content_type='application/json',
                method='POST',headers=headers,retries=2,timeout='55s',schedule_id=row['schedule_id'])
            client.schedule.pause(row['schedule_id'])
        print(json.dumps({'prepared':ids,'flow_key':queue,'paused':True}));return
    schedules={s.schedule_id:asdict(s) for s in client.schedule.list() if s.schedule_id in ids.values()}
    if args.command=='activate':
        if len(schedules)!=len(ids):p.error('prepare all three schedules first')
        for row in expected:
            actual=schedules[row['schedule_id']]
            if actual['destination']!=target or (actual['flow_control'] or {}).get('key')!=queue or (actual['flow_control'] or {}).get('parallelism')!=1 or actual['cron']!=row['cron'] or actual['queue']:
                p.error('schedule configuration differs from the reviewed plan')
            client.schedule.resume(row['schedule_id'])
        print(json.dumps({'activated':ids,'flow_key':queue}));return
    if args.command=='pause':
        for sid in schedules:client.schedule.pause(sid)
        print(json.dumps({'paused':ids}));return
    if args.command=='probe':
        result=client.message.publish_json(flow_control={'key':queue,'parallelism':1},url=target,body={'operation':args.operation},
            headers=headers,retries=2,timeout='55s',label='csr-'+args.environment+'-probe')
        print(json.dumps(asdict(result)));return
    # Do not expose provider-stored forwarded authentication headers.
    print(json.dumps({'flow_key':queue,
        'schedules':[{k:v for k,v in s.items() if k in {'schedule_id','cron','destination','flow_control','paused','retries','last_schedule_time','next_schedule_time','last_schedule_states'}} for s in schedules.values()]},indent=2,default=lambda value:value.value))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # SDK error messages may contain request details; report the class only.
        raise SystemExit('scheduler administration failed: '+type(exc).__name__) from None
