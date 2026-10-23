"""A cheap scheduling pass with independent, bounded executor lanes and lease heartbeats."""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from app.db.models import CoordinatorWork, CoordinatorOutput
from app.services import coordinator as service, background_components as components

log = logging.getLogger(__name__)
wake = threading.Event()
capture_wake = threading.Event()


class Runtime:
    def __init__(self, slots: int = 2):
        self.slots = slots
        self.pools = {kind: ThreadPoolExecutor(max_workers=slots, thread_name_prefix=f"executor-{kind}")
                      for kind in ("mind", "recall", "memory", "observation", "cognition", "conclusion")}
        self.active = {kind: {} for kind in self.pools}
        self.closed = False
        self.last_prune = 0.0

    def _reap_and_renew(self):
        for kind, pending in self.active.items():
            for key, (future, token, generation) in list(pending.items()):
                if future.done():
                    del pending[key]
                    try:
                        future.result()
                    except Exception:
                        log.exception("executor %s failed; durable work remains recoverable", kind)
                elif kind != "conclusion":
                    service.renew(key, token, generation)

    def tick(self):
        if self.closed:
            return False
        self._reap_and_renew()
        service.expire_due()
        # Recovery and result decisions have their own capacity, never the slow mind's lane.
        pending = self.active["conclusion"]
        if len(pending) < self.slots:
            with service.SessionLocal() as db:
                orphan = db.scalar(select(CoordinatorWork.id).where(
                    CoordinatorWork.kind == "mind", CoordinatorWork.status.in_(service.TERMINAL),
                    CoordinatorWork.id.not_in([key for key in pending if isinstance(key, uuid.UUID)]),
                    ~select(CoordinatorOutput.id).where(CoordinatorOutput.work_id == CoordinatorWork.id,
                        CoordinatorOutput.phase == "follow_up").exists())
                    .order_by(CoordinatorWork.created_at).limit(1))
            if orphan:
                pending[orphan] = (self.pools["conclusion"].submit(service._conclude, orphan), None, None)
            elif "delivery" not in pending:
                pending["delivery"] = (self.pools["conclusion"].submit(service.recover_follow_up), None, None)
        components.schedule_due()
        import time
        if time.monotonic() - self.last_prune > 60:
            components.prune_receipts()
            self.last_prune = time.monotonic()
        dispatched = False
        for kind in ("recall", "mind", "cognition", "memory", "observation"):
            if not components.enabled(kind):
                continue
            pending = self.active[kind]
            for _ in range(self.slots - len(pending)):
                token = uuid.uuid4().hex
                with service.SessionLocal() as db:
                    work, ended = service._claim(db, token, (kind,), exclude_ids=tuple(pending))
                    db.commit()
                    if work is None:
                        break
                    db.expunge(work)
                if not ended:
                    pending[work.id] = (self.pools[kind].submit(service.execute_claim, work, token),
                                        token, work.cancellation_generation)
                dispatched = True
        return dispatched

    def close(self):
        """Stop claims, fence running jobs, and drain bounded executors. Network calls obey their owner timeouts."""
        self.closed = True
        # Relinquish with a new generation, so completion from the old process cannot be accepted.
        from sqlalchemy import update
        for kind, pending in self.active.items():
            if kind == "conclusion":
                continue
            for key, (_, token, generation) in pending.items():
                with service.SessionLocal() as db:
                    db.execute(update(CoordinatorWork).where(CoordinatorWork.id == key,
                        CoordinatorWork.status == "running", CoordinatorWork.lease_owner == token,
                        CoordinatorWork.cancellation_generation == generation).values(
                        status="queued", lease_owner=None, lease_expires_at=None,
                        cancellation_generation=CoordinatorWork.cancellation_generation + 1))
                    db.commit()
        for pool in self.pools.values():
            pool.shutdown(wait=True, cancel_futures=True)


_instance = None

def runtime():
    global _instance
    if _instance is None or _instance.closed:
        _instance = Runtime(getattr(service.get_settings(), "coordinator_executor_slots", 2))
    return _instance


def close():
    if _instance is not None:
        _instance.close()
