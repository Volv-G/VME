"""One request at a time, per match.

Every handler that changes a match does the same three things: load
`match.json`, change something, write the whole file back. Two of those
running at once lose one of the changes, because the second one loaded
before the first one saved and then wrote its own idea of the match over
the top.

That is not hypothetical and it is not rare. The editor saves the
playhead a second or so after it stops moving, a `GET` on a match saves
it too (the scan may have found new clips), and the operator is tagging
events throughout. The symptom is the worst kind: the event appears -
the response that created it is correct - and vanishes when the next one
is added, because by then the file no longer has it.

Making the write atomic (`Match.save`) fixed torn files but not this:
each writer's file is complete, it is simply missing somebody else's
work. The fix has to cover the read as well as the write, so the lock is
held for the whole request.

Scope and cost
--------------

Per match, so two matches are never serialized against each other, and
only for handlers that touch a match's own record. Deliberately NOT on
media streaming: a range request for a video can run for as long as the
client keeps reading, and holding a match's lock for that would freeze
every edit of it.

The uvicorn worker runs these handlers in a thread pool, so an ordinary
`threading.Lock` is the right tool. A second process would not be
covered - there is only one here, and a file lock would be the answer if
that changes.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

# Keyed by the four path segments that identify a match. Only ever
# touched from the event loop (see below), so it needs no mutex of its
# own.
_LOCKS: dict[tuple[str, str, str, str], asyncio.Lock] = {}


def _lock_for(key: tuple[str, str, str, str]) -> asyncio.Lock:
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


async def match_lock_dep(
    team: str, tournament: str, date: str, match: str
) -> AsyncIterator[None]:
    """FastAPI dependency: hold the match's lock for the whole request.

    A dependency with `yield` wraps the endpoint, so the lock is taken
    before the handler runs and released after its response is built -
    exactly the span that must not interleave.

    ### Why this is async, and why that matters

    The first version was an ordinary sync dependency holding a
    `threading.RLock`. FastAPI runs those through a thread pool, and the
    setup and the teardown are not guaranteed to land on the same
    thread - so the release came from a thread that had never acquired
    it and raised `RuntimeError: cannot release un-acquired lock` after
    the response had already gone out. The failure was quiet from the
    client's side and poisonous underneath: the lock stayed held, a
    request reusing that pooled thread re-entered it (an RLock is
    reentrant *per thread*, so it granted no exclusion at all), and one
    landing on any other thread would have waited forever.

    An async dependency runs on the event loop, so acquire and release
    are the same task, and an `asyncio.Lock` is the right primitive
    there. The endpoint itself is sync and still runs in the thread
    pool; the loop is free while it does, so other matches carry on.
    """
    async with _lock_for((team, tournament, date, match)):
        yield
