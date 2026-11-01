"""Protected read-only operations, hosted by the existing research function."""
import os
from api.scan import handler as ScanHandler, _authorized
from src.product import product_mode
from src.research_journal import open_journal, RevisionConflict
from src.bsc.scanner import run
from src.bsc.scanner import publish_current,stream_for
from urllib.parse import urlparse,parse_qs


class handler(ScanHandler):
    def do_GET(self):
        self._send(405,{'error':'method_not_allowed','execution_enabled':False})

    def do_POST(self):
        if product_mode()!='bsc':
            self._send(409,{'error':'bsc_product_not_enabled'});return
        if not _authorized(self.headers.get('Authorization'),os.getenv('CSR_WORKER_TOKEN')):
            self._send(401,{'error':'unauthorized'});return
        journal=None
        try:
            journal=open_journal(os.getenv('CSR_JOURNAL_ROOT','/tmp/radar-journal'))
            operation=parse_qs(urlparse(self.path).query).get('operation',['collect'])[0]
            if operation=='publish':
                published=publish_current(journal)
                self._send(200,{'published':published,'revision':stream_for(journal).load()[0]})
            elif operation=='collect':self._send(200,run(journal,publish=True))
            else:self._send(400,{'error':'unknown_operation'})
        except RevisionConflict:
            self._send(409,{'error':'stream_revision_conflict','retryable':True})
        except Exception as exc:
            self._send(503,{'error':'bsc_collection_incomplete','error_type':type(exc).__name__,
                            'execution_enabled':False})
        finally:
            if journal:journal.close()
