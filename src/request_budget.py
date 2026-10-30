"""Per-scan upstream deadline and measured request counts, shared by workers."""
from contextvars import ContextVar
from threading import Lock
import time

CURRENT = ContextVar("radar_request_budget", default=None)


class RequestBudget:
    def __init__(self, seconds=40, max_requests=1600):
        self.deadline = time.monotonic()+seconds
        self.max_requests = max_requests
        self.calls = self.failures = 0
        self.seconds = 0.0
        self.lock = Lock()

    def begin(self):
        with self.lock:
            remaining = self.deadline-time.monotonic()
            if remaining<=0 or self.calls>=self.max_requests:
                raise TimeoutError("scan upstream budget exhausted")
            self.calls += 1
            return max(0.001,min(8,remaining))

    def finish(self, elapsed, failed):
        with self.lock:
            self.seconds += elapsed
            self.failures += int(failed)

    def report(self):
        return {"requests":self.calls,"failed_requests":self.failures,"request_seconds":round(self.seconds,3)}
