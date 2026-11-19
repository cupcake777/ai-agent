"""The FIFO claim's pending-row reads wait off the owner loop.

Every turn's drain reads the session's pending admissions before its claim (and re-reads the head
after an awaited preclaim check). On slow storage that read used to stop the whole owner loop, every
other session's turn, Stop and socket included. Real SessionAuthority + state.db; the read blocks on
a threading event that only the owner loop can release, proving the loop remains runnable without a
wall-clock scheduling threshold. The claim still takes the head the read validated.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.fixtures.storage_seed import seed_session
from tests.gateway.fixtures.storage_handles import close_storage_handles, storage_handle

@pytest.mark.asyncio
async def test_claim_pending_read_keeps_the_owner_loop_ticking(tmp_path, monkeypatch):
    from gateway.session_authority import LiveSession, SessionAuthority
    from gateway.session_contract import SessionRef
    from persistence.sqlite.authority.admissions import SQLiteAdmissionRepository
    db = storage_handle(tmp_path)
    seed_session(db, 's', 'cron')
    authority = SessionAuthority(SimpleNamespace(_draining=False, adapters={}), profile_id='p', instance_id='t',
                                 persistence=db, epoch=db.runtime.begin_epoch(instance_id='t'))
    live = authority.sessions['s'] = LiveSession(SimpleNamespace(platform=None, user_id='u'), 'route')
    row, _ = db.admissions.admit(epoch=authority.epoch, principal_id='u', session_id='s',
                                 request_id='r1', payload={'text': 'x'})
    real, slow = SQLiteAdmissionRepository.list, threading.Event()
    read_entered, release_read = threading.Event(), threading.Event()

    def slow_list(*args, **kwargs):
        if slow.is_set():
            read_entered.set()
            # The event loop must release this worker. If the read regresses onto that loop, the
            # handshake cannot complete; the timeout is only a deadlock guard, not a performance bar.
            assert release_read.wait(30), 'owner loop could not run while the claim read was pending'
        return real(*args, **kwargs)
    monkeypatch.setattr(SQLiteAdmissionRepository, 'list', slow_list)

    async def release_from_owner_loop():
        while not read_entered.is_set():
            await asyncio.sleep(0)
        release_read.set()

    slow.set()
    releaser = asyncio.create_task(release_from_owner_loop())
    try:
        claimed, first = await asyncio.wait_for(authority._claim_next(SessionRef('p', 's'), live), 35)
        await releaser
    finally:
        slow.clear()
        release_read.set()
        releaser.cancel()
        db.close()
    assert first['admission_id'] == row.admission_id
    assert claimed and claimed['admission_id'] == row.admission_id and claimed['status'] == 'started'
