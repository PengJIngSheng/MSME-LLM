"""Shared, race-free construction of PGVector stores.

langchain_postgres registers its ORM tables (`langchain_pg_collection`,
`langchain_pg_embedding`) on a module-level SQLAlchemy declarative base the
first time a PGVector is constructed. That registration is not thread-safe.

server.py builds the memory store and the knowledge store concurrently:

    await asyncio.gather(_get_memory(), _get_knowledge())

Each runs in its own worker thread, so on the first chat turn after a restart
both threads enter PGVector.__init__ at once and race on the shared base:

    [Knowledge RAG] Retrieval Error: Table 'langchain_pg_collection' is
    already defined for this MetaData instance.

Both retrievals then return empty, and the turn is answered with no knowledge
base and no long-term memory at all -- silently, because every call site
degrades gracefully. It reproduces on every restart and only on the first turn,
which is why it looked intermittent.

Serialising construction behind one process-wide lock fixes it: sequential
construction is fine, only the concurrent case collides. Stores are cached per
collection so the lock is contended once per process.
"""

from __future__ import annotations

import threading
from typing import Dict

from langchain_postgres.vectorstores import PGVector

# One lock for every store in the process, not one per module: the collision is
# between two *different* modules constructing at the same time, so a per-module
# lock would not exclude them from each other.
_LOCK = threading.Lock()
_STORES: Dict[str, PGVector] = {}


def get_store(
    *,
    collection_name: str,
    connection_uri: str,
    embeddings,
    embedding_length: int = 768,
) -> PGVector:
    """Return the process-wide PGVector for `collection_name`, building it once.

    Double-checked locking: the fast path stays lock-free after warm-up, which
    matters because this sits inline on every chat turn.
    """
    store = _STORES.get(collection_name)
    if store is not None:
        return store

    with _LOCK:
        store = _STORES.get(collection_name)
        if store is None:
            store = PGVector(
                embeddings=embeddings,
                embedding_length=embedding_length,
                collection_name=collection_name,
                connection=connection_uri,
                use_jsonb=True,
            )
            _STORES[collection_name] = store
    return store


def reset_store(collection_name: str) -> None:
    """Drop the cached store so the next call rebuilds it."""
    with _LOCK:
        _STORES.pop(collection_name, None)


# How many connections to open per store at startup. SQLAlchemy's default
# pool_size is 5, so filling that covers the first burst; anything beyond it
# comes from max_overflow, which is cheap once the server is warm.
PREWARM_CONNECTIONS = 5


def prewarm(store: PGVector, connections: int = PREWARM_CONNECTIONS) -> int:
    """Open and return `connections` pooled connections so the pool is hot.

    Without this, the first turn that needs more than one connection pays TCP
    connect plus PostgreSQL authentication per connection. Measured here, the
    first burst of 5 concurrent retrievals took 3.22s and four of them blew the
    3-second retrieval budget and were dropped -- so the first users after every
    restart silently got no knowledge base and no memory.

    Connections are checked out simultaneously (not in a loop) because checking
    out one at a time would reuse the same pooled connection over and over and
    warm nothing. Returns the number successfully opened.
    """
    engine = getattr(store, "_engine", None) or getattr(store, "_async_engine", None)
    if engine is None:
        return 0

    held = []
    opened = 0
    try:
        for _ in range(max(1, connections)):
            try:
                conn = engine.connect()
                conn.exec_driver_sql("SELECT 1")
                held.append(conn)
                opened += 1
            except Exception:
                # Pool exhausted or database busy: stop, keep what we have.
                break
    finally:
        # Returning them to the pool is what leaves them warm and reusable.
        for conn in held:
            try:
                conn.close()
            except Exception:
                pass
    return opened
