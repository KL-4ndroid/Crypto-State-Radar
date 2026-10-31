"""Durable per-source cursors and token leases on the existing journal backend."""
from datetime import timedelta,datetime,timezone
import json
from pathlib import Path
from uuid import uuid4

from .contracts import RawObservation,digest,encode,stamp
from ..retry_policy import utc,retry_at


class StaleAcquisition(RuntimeError):pass


def install_acquisition(journal):
    if journal.pg:raise RuntimeError('PostgreSQL acquisition migration must be explicit')
    text=(Path(__file__).resolve().parents[2]/'schema/v33_acquisition.sql').read_text(encoding='utf-8')
    portable='\n'.join(line for line in text.split('-- POSTGRES_ONLY')[0].splitlines() if not line.lstrip().startswith('--'))
    with journal.transaction():
        for statement in portable.split(';'):
            if statement.strip():journal.sql(statement)
        for operation in ('UPDATE','DELETE'):
            journal.sql(f"CREATE TRIGGER IF NOT EXISTS csr_v33_items_no_{operation} BEFORE {operation} ON csr_v33_acquisition_items BEGIN SELECT RAISE(ABORT,'acquisition history is immutable'); END")


class AcquisitionQueue:
    def __init__(self,store):
        if store.namespace!='SHADOW':raise ValueError('live acquisition requires an isolated SHADOW namespace')
        self.store=store;self.journal=store.journal;self.prefix=store.prefix

    def ensure(self,source,symbol,start_ms,now):
        cursor={'origin_ms':start_ms,'next_time_ms':start_ms,'window_start_ms':start_ms,'next_id':None,
                'quote_delta':'0','buy_quote':'0','sell_quote':'0','page_ids':[],'trade_count':0}
        with self.journal.transaction():
            self.journal.sql('INSERT INTO csr_v33_acquisition VALUES(?,?,?,?,0,?,?,0,NULL,NULL,NULL) ON CONFLICT(namespace,run_id,source,symbol) DO NOTHING',
                (*self.prefix,source,symbol,encode(cursor),stamp(now)))

    def status(self,source,symbol):
        row=self.journal.sql('SELECT revision,cursor_payload,next_due,attempts,lease_token,lease_until,last_error FROM csr_v33_acquisition WHERE namespace=? AND run_id=? AND source=? AND symbol=?',(*self.prefix,source,symbol)).fetchone()
        return dict(zip(('revision','cursor','next_due','attempts','lease_token','lease_until','last_error'),(row[0],json.loads(row[1]),*row[2:]))) if row else None

    def summary(self):
        rows=self.journal.sql('SELECT source,symbol,revision,cursor_payload,next_due,last_error FROM csr_v33_acquisition WHERE namespace=? AND run_id=? ORDER BY symbol,source',self.prefix).fetchall()
        output=[]
        for source,symbol,revision,raw,due,error in rows:
            cursor=json.loads(raw)
            output.append({'source':source,'symbol':symbol,'revision':revision,'next_due':due,'last_error':error,
                'next_time_ms':cursor['next_time_ms'],'window_start_ms':cursor['window_start_ms'],
                'next_trade_id':cursor['next_id'],'partial_pages':len(cursor['page_ids'])})
        return output

    def claim(self,source,symbol,now):
        with self.journal.transaction():
            suffix=' FOR UPDATE' if self.journal.pg else ''
            row=self.journal.sql('SELECT revision,cursor_payload,next_due,attempts,lease_until FROM csr_v33_acquisition WHERE namespace=? AND run_id=? AND source=? AND symbol=?'+suffix,(*self.prefix,source,symbol)).fetchone()
            if not row or row[2]>stamp(now) or (row[4] and row[4]>stamp(now)):return None
            token=uuid4().hex
            self.journal.sql('UPDATE csr_v33_acquisition SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE namespace=? AND run_id=? AND source=? AND symbol=?',
                (token,stamp(utc(now)+timedelta(seconds=120)),*self.prefix,source,symbol))
            return {'source':source,'symbol':symbol,'revision':row[0],'cursor':json.loads(row[1]),'attempts':row[3]+1,'token':token}

    def _check(self,claim,now):
        suffix=' FOR UPDATE' if self.journal.pg else ''
        row=self.journal.sql('SELECT revision,lease_token,lease_until FROM csr_v33_acquisition WHERE namespace=? AND run_id=? AND source=? AND symbol=?'+suffix,(*self.prefix,claim['source'],claim['symbol'])).fetchone()
        if not row or row[0]!=claim['revision'] or row[1]!=claim['token'] or not row[2] or row[2]<=stamp(now):
            raise StaleAcquisition('acquisition lease was superseded')

    def _observation(self,source,symbol,item_key,effective,values,blob,observed):
        content=digest(values)
        old=self.journal.sql('SELECT record_id FROM csr_v33_acquisition_items WHERE namespace=? AND run_id=? AND source=? AND symbol=? AND item_key=? AND content_hash=?',(*self.prefix,source,symbol,item_key,content)).fetchone()
        if old:return old[0],False
        raw=RawObservation(source,symbol,values.get('raw_timestamp',item_key),stamp(effective),stamp(observed),stamp(observed),content,
            {'values':values,'archive':blob},data_version='v33-acquisition-1').row()
        self.journal._insert(self.store.kind('raw'),raw['record_id'],raw)
        self.journal.sql('INSERT INTO csr_v33_acquisition_items VALUES(?,?,?,?,?,?,?,?,?)',
            (*self.prefix,source,symbol,item_key,content,stamp(effective),stamp(observed),raw['record_id']))
        return raw['record_id'],True

    def finish(self,claim,items,cursor,manifest,observed,next_due,completed_at=None):
        """Archive bytes must already be durable; records and cursor commit together."""
        with self.journal.transaction():
            completed_at=completed_at or datetime.now(timezone.utc)
            self._check(claim,completed_at)
            ids=[];inserted=0
            for item in items:
                rid,new=self._observation(item.get('source',claim['source']),claim['symbol'],item['key'],item['effective_time'],item['values'],manifest['archive'],observed)
                ids.append(rid);inserted+=int(new)
            receipt=dict(manifest,source=claim['source'],symbol=claim['symbol'],revision=claim['revision']+1,
                availability_time=stamp(observed),committed_at=stamp(completed_at),record_ids=ids,new_records=inserted)
            self.journal._insert(self.store.kind('acquisitions'),digest([claim['source'],claim['symbol'],claim['revision']+1]),receipt)
            self.journal.sql('UPDATE csr_v33_acquisition SET revision=revision+1,cursor_payload=?,next_due=?,attempts=0,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE namespace=? AND run_id=? AND source=? AND symbol=?',
                (encode(cursor),stamp(next_due),*self.prefix,claim['source'],claim['symbol']))
        return receipt

    def fail(self,claim,now,error_type,delay_seconds=None,archive=None):
        with self.journal.transaction():
            self._check(claim,now)
            # Never store raw provider errors, URLs with credentials, or headers.
            row={'source':claim['source'],'symbol':claim['symbol'],'attempt':claim['attempts'],
                 'at':stamp(now),'error_type':error_type,'archive':archive}
            self.journal._insert(self.store.kind('acquisition_failures'),claim['token'],row)
            due=stamp(utc(now)+timedelta(seconds=delay_seconds)) if delay_seconds is not None else stamp(retry_at(now,claim['attempts'],claim['source']+':'+claim['symbol']))
            self.journal.sql('UPDATE csr_v33_acquisition SET next_due=?,lease_token=NULL,lease_until=NULL,last_error=? WHERE namespace=? AND run_id=? AND source=? AND symbol=?',
                (due,error_type,*self.prefix,claim['source'],claim['symbol']))

    def item_at(self,source,symbol,when,available_at,exact=False):
        op='=' if exact else '<='
        rows=self.journal.sql('SELECT record_id,effective_time,availability_time FROM csr_v33_acquisition_items WHERE namespace=? AND run_id=? AND source=? AND symbol=? AND effective_time'+op+'? AND availability_time<=? ORDER BY effective_time DESC,availability_time DESC,content_hash DESC LIMIT 2',
            (*self.prefix,source,symbol,stamp(when),stamp(available_at))).fetchall()
        if len(rows)>1 and rows[0][1:]==rows[1][1:]:raise ValueError('ambiguous source revision')
        return self.journal.record(self.store.kind('raw'),rows[0][0]) if rows else None
