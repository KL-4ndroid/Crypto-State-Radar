"""V3.3 adapter on the existing ResearchJournal transaction/immutability contract."""
from pathlib import Path
import json
from .contracts import CONTRACT_VERSION, REFERENCE_FEATURES, digest, encode, evaluation_key, scope, snapshot, stamp
from .registry import validate_rule
from .fsm import advance, initial, TERMINALS
from ..research_journal import RevisionConflict


def install_local(journal):
    if journal.pg:
        raise RuntimeError('PostgreSQL V3.3 migration must be explicit')
    schema=(Path(__file__).resolve().parents[2]/'schema/v33_contract.sql').read_text(encoding='utf-8')
    with journal.transaction():
        portable='\n'.join(line for line in schema.split('-- POSTGRES_ONLY')[0].splitlines() if not line.lstrip().startswith('--'))
        for statement in portable.split(';'):
            # The shared portion has no procedural SQL or semicolons in comments.
            if statement.strip():journal.sql(statement)
        for table in ('csr_v33_features','csr_v33_evaluations','csr_v33_events'):
            for action in ('UPDATE','DELETE'):
                journal.sql(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'V3.3 history is immutable'); END")


class ContractStore:
    def __init__(self,journal,namespace,run_id,allow_production=False):
        scope(namespace,run_id)
        if namespace=='PRODUCTION' and not allow_production:
            raise ValueError('production writes require an explicitly authorized live adapter')
        self.journal=journal;self.namespace=namespace;self.run_id=run_id
        self.prefix=(namespace,run_id)

    def kind(self,name):
        return 'v33:'+self.namespace+':'+digest(self.run_id)+':'+name

    def put_rule(self,rule):
        validate_rule(rule)
        if self.namespace=='PRODUCTION' and rule['status']!='CHAMPION':
            raise ValueError('experimental rules cannot enter production namespace')
        with self.journal.transaction():
            self.journal.sql('SELECT id FROM csr_runtime WHERE id=1'+(' FOR UPDATE' if self.journal.pg else '')).fetchone()
            self.journal._insert('v33:registry:rules',rule['rule_version'],rule)

    def put_raw(self,observation):
        row=observation.row()
        if self.namespace=='PRODUCTION' and row['availability_basis']=='ASSUMED':
            raise ValueError('assumed availability cannot enter production')
        with self.journal.transaction():self.journal._insert(self.kind('raw'),row['record_id'],row)
        return row['record_id']

    def put_feature(self,feature):
        row=feature.row()
        source_times=[]
        for source_id in row['source_ids']:
            source=self.journal.record(self.kind('raw'),source_id)
            if not source or source['effective_time'] is None:
                raise ValueError('missing source or unknown effective-time semantics')
            if source['availability_time']>row['availability_time'] or source['effective_time']>row['source_time']:
                raise ValueError('feature predates its source')
            if not source['source_integrity'] and row['value'] is not None:
                raise ValueError('bad source cannot produce an available feature')
            if source_id in row['reference_source_ids'] and source['effective_time']>=row['asof_time']:
                raise ValueError('normalization reference observations must strictly precede asof')
            source_times.append(source['effective_time'])
        if row['source_time']!=max(source_times):
            raise ValueError('source_time must reflect actual lineage, not a refreshed clock')
        with self.journal.transaction():
            old=self.journal.sql('SELECT payload FROM csr_v33_features WHERE namespace=? AND run_id=? AND feature_id=?',(*self.prefix,row['feature_id'])).fetchone()
            if old and old[0]!=encode(row):raise ValueError('feature identity conflict')
            self.journal.sql('INSERT INTO csr_v33_features VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(namespace,run_id,feature_id) DO NOTHING',
                (*self.prefix,row['feature_id'],row['feature_name'],row['feature_version'],row['symbol'],row['asof_time'],row['availability_time'],encode(row)))
        return row['feature_id']

    def feature_rows(self,ids):
        rows=[]
        for feature_id in sorted(set(ids)):
            row=self.journal.sql('SELECT payload FROM csr_v33_features WHERE namespace=? AND run_id=? AND feature_id=?',(*self.prefix,feature_id)).fetchone()
            if not row:raise ValueError('feature is absent in this namespace/run')
            rows.append(json.loads(row[0]))
        return rows

    def asof(self,symbol,feature_name,feature_version,when):
        when=stamp(when)
        rows=self.journal.sql('''SELECT payload FROM csr_v33_features WHERE namespace=? AND run_id=?
            AND symbol=? AND feature_name=? AND feature_version=? AND asof_time<=? AND availability_time<=?
            ORDER BY asof_time DESC,availability_time DESC,feature_id DESC LIMIT 2''',
            (*self.prefix,symbol,feature_name,feature_version,when,when)).fetchall()
        if len(rows)>1:
            a,b=(json.loads(r[0]) for r in rows)
            if (a['asof_time'],a['availability_time'])==(b['asof_time'],b['availability_time']):
                raise ValueError('ambiguous feature revision; pin feature_id')
        return json.loads(rows[0][0]) if rows else None

    def evaluate(self,symbol,when,rule_version,feature_ids,expected_revision=None,supersedes=None):
        when=stamp(when)
        rule=self.journal.record('v33:registry:rules',rule_version)
        if not rule or rule['status']=='RETIRED':raise ValueError('active registered rule required')
        if self.namespace=='PRODUCTION' and rule['status']!='CHAMPION':raise ValueError('production requires a champion')
        features=self.feature_rows(feature_ids)
        snapshot_hash,values=snapshot(features,symbol,when)
        references=REFERENCE_FEATURES | set(rule['parameters']['gate'].get('additional_past_only_features',[]))
        if any(f['value'] is not None and f['feature_name'] in references and not f.get('reference_source_ids') for f in features):
            raise ValueError('normalization feature requires past-only reference lineage')
        sources=[self.journal.record(self.kind('raw'),sid) for sid in sorted({sid for f in features for sid in f['source_ids']})]
        key=evaluation_key(symbol,when,rule_version,snapshot_hash)
        input_hash=digest([rule,features,supersedes])
        args=(*self.prefix,symbol,rule_version)
        with self.journal.transaction():
            # Lock only this research stream; long research batches must not
            # serialize the unrelated live scanner on its csr_runtime row.
            suffix=' FOR UPDATE' if self.journal.pg else ''
            self.journal.sql('INSERT INTO csr_v33_heads VALUES(?,?,?,?,0,NULL,?) ON CONFLICT(namespace,run_id,symbol,rule_version) DO NOTHING',(*args,encode(initial())))
            head=self.journal.sql('SELECT revision,last_time,payload FROM csr_v33_heads WHERE namespace=? AND run_id=? AND symbol=? AND rule_version=?'+suffix,args).fetchone()
            previous=self.journal.sql('SELECT input_hash,payload FROM csr_v33_evaluations WHERE namespace=? AND run_id=? AND evaluation_key=?',(*self.prefix,key)).fetchone()
            if previous:
                if previous[0]!=input_hash:raise ValueError('evaluation identity conflict')
                return json.loads(previous[1])
            if supersedes:
                if self.namespace not in {'REPLAY','BACKTEST'}:raise ValueError('corrections require a research namespace')
                reference=self.journal.sql('SELECT symbol,evaluation_time FROM csr_v33_evaluations WHERE evaluation_id=?',(supersedes,)).fetchone()
                if not reference or reference!=(symbol,when):raise ValueError('superseded evaluation must match symbol/time')
            revision=head[0] if head else 0
            if expected_revision is not None and expected_revision!=revision:raise RevisionConflict('stale V3.3 stream revision')
            if head and head[1] and when<=head[1]:raise ValueError('out-of-order correction requires a separate research run')
            old=json.loads(head[2]) if head else initial()
            # Source integrity is derived from immutable lineage, never supplied
            # as a caller-controlled quality flag.
            values['source_integrity']=bool(sources) and all(s and s['source_integrity'] and s['effective_time'] is not None and s['availability_basis']!='ASSUMED' for s in sources)
            state,result,events=advance(old,values,features,when,key,rule['parameters'])
            # Parent checks inspect the full durable chain, including earlier evaluations.
            local={}
            for event in events:
                kind=event['event_type'];parent=event['parent_event_id']
                if kind in {'SHORT_READY','SHORT_TRIGGERED',*TERMINALS}:
                    row=local.get(parent)
                    if row is None and parent:
                        found=self.journal.sql('SELECT payload FROM csr_v33_events WHERE namespace=? AND run_id=? AND event_id=?',(*self.prefix,parent)).fetchone()
                        row=json.loads(found[0]) if found else None
                    required={'SHORT_READY':{'SHORT_SETUP'},'SHORT_TRIGGERED':{'SHORT_READY'},'INVALIDATED':{'SHORT_SETUP','SHORT_READY','SHORT_TRIGGERED'},'EXPIRED':{'SHORT_SETUP','SHORT_READY','SHORT_TRIGGERED'}}[kind]
                    if not row or row['event_type'] not in required or row['chain_id']!=event['chain_id'] or row['symbol']!=symbol or row['rule_version']!=rule_version or row['evaluation_time']>when:
                        raise ValueError('missing or wrong signal-chain parent')
                event.update(evaluation_key=key,namespace=self.namespace,run_id=self.run_id,symbol=symbol,rule_version=rule_version)
                local[event['event_id']]=event
            result.update(symbol=symbol,evaluation_time=when,rule_version=rule_version,
                feature_snapshot_hash=snapshot_hash,evaluation_key=key,journal_namespace=self.namespace,
                run_id=self.run_id,contract_version=CONTRACT_VERSION,data_versions=sorted({s['data_version'] for s in sources}),
                evaluation_id=digest([self.namespace,self.run_id,key]),
                data_revision=digest(sorted((s['record_id'],s['source_revision']) for s in sources)),
                feature_versions={f['feature_name']:f['feature_version'] for f in features},
                rule_status=rule['status'],events=events,revision=revision+1,supersedes_evaluation_id=supersedes)
            self.journal.sql('INSERT INTO csr_v33_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (*self.prefix,key,input_hash,result['evaluation_id'],symbol,when,rule_version,snapshot_hash,supersedes,encode(result)))
            for e in events:
                self.journal.sql('INSERT INTO csr_v33_events VALUES(?,?,?,?,?,?,?,?,?)',
                    (*self.prefix,e['event_id'],key,e['chain_id'],e['event_type'],e['sequence_no'],e['parent_event_id'],encode(e)))
            self.journal.sql('''INSERT INTO csr_v33_heads VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(namespace,run_id,symbol,rule_version) DO UPDATE SET
                revision=excluded.revision,last_time=excluded.last_time,payload=excluded.payload''',
                (*args,revision+1,when,encode(state)))
        return result
