"""Allowlisted, bounded JSON-RPC reader. No signing or broadcast methods exist."""
import json
import time
from urllib.parse import urlparse
import requests
from ..request_budget import RequestBudget

READ_METHODS = frozenset({'eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber',
                          'eth_getBlockReceipts', 'eth_getCode','eth_getLogs'})


class ReadRPC:
    def __init__(self, urls, budget=None, max_body=4*1024*1024):
        if not 1 <= len(urls) <= 3:
            raise ValueError('one to three RPC origins required')
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('RPC must be HTTPS without URL credentials')
        self.urls, self.budget, self.max_body = tuple(urls), budget or RequestBudget(max_requests=12), max_body

    def call(self, method, params):
        if method not in READ_METHODS:
            raise ValueError('non-read RPC method is forbidden')
        for url in self.urls:
            timeout = self.budget.begin()
            started, failed = time.monotonic(), True
            try:
                with requests.post(url, json={'jsonrpc':'2.0','id':1,'method':method,'params':params},
                                   timeout=timeout, stream=True, allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise RuntimeError('RPC HTTP failure')
                    chunks, size = [], 0
                    for chunk in response.iter_content(32768):
                        size += len(chunk)
                        if size > self.max_body or time.monotonic() > self.budget.deadline:
                            raise TimeoutError('RPC response budget exceeded')
                        chunks.append(chunk)
                    row = json.loads(b''.join(chunks))
                    if row.get('jsonrpc') != '2.0' or row.get('id') != 1 or row.get('error') or row.get('result') is None:
                        raise RuntimeError('RPC result unavailable')
                    failed = False
                    return row['result']
            except (requests.RequestException, RuntimeError, ValueError, TimeoutError):
                pass
            finally:
                self.budget.finish(time.monotonic()-started, failed)
        raise RuntimeError('read RPC unavailable; no cursor advanced')
