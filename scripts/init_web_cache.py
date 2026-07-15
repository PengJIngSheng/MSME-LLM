"""
One-shot script: create the web_content_cache table and indexes in PostgreSQL.

Usage:
    python scripts/init_web_cache.py

The script reads DATABASE_URL from env or falls back to the pgvector URI in config.
"""

import asyncio
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import cfg

_EMBED_DIM = 768

_DDL_STEPS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""CREATE TABLE IF NOT EXISTS web_content_cache (
        id          BIGSERIAL PRIMARY KEY,
        url         TEXT      NOT NULL,
        url_hash    CHAR(32)  NOT NULL,
        title       TEXT      DEFAULT '',
        chunk_text  TEXT      NOT NULL,
        chunk_index INTEGER   DEFAULT 0,
        embedding   vector({_EMBED_DIM}),
        domain      TEXT      DEFAULT '',
        fetched_at  TIMESTAMPTZ DEFAULT NOW(),
        expires_at  TIMESTAMPTZ,
        UNIQUE (url_hash, chunk_index)
    )""",
    "CREATE INDEX IF NOT EXISTS wcc_url_hash_idx ON web_content_cache (url_hash)",
    "CREATE INDEX IF NOT EXISTS wcc_expires_idx  ON web_content_cache (expires_at)",
]

_IVF_INDEX = (
    "CREATE INDEX IF NOT EXISTS wcc_embedding_idx ON web_content_cache "
    "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50)"
)


def _pg_dsn(uri: str) -> str:
    return re.sub(r"^postgresql\+psycopg", "postgresql", uri)


async def main() -> None:
    db_uri = os.environ.get("DATABASE_URL") or cfg.pgvector_connection_uri
    if not db_uri:
        print("ERROR: No database URI found. Set DATABASE_URL or configure pgvector in config.")
        sys.exit(1)

    dsn = _pg_dsn(db_uri)
    print(f"Connecting to: {re.sub(r':([^/@]+)@', ':***@', dsn)}")

    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        for stmt in _DDL_STEPS:
            await conn.execute(stmt)
            print(f"  OK: {stmt[:60].strip()}...")

        # IVFFlat requires at least a few rows; skip if table is empty
        cur = await conn.execute("SELECT COUNT(*) FROM web_content_cache")
        row = await cur.fetchone()
        count = row[0] if row else 0
        if count >= 50:
            await conn.execute(_IVF_INDEX)
            print("  OK: IVFFlat embedding index created.")
        else:
            print(f"  SKIP: IVFFlat index (needs >=50 rows, have {count}). Re-run after first crawls.")

    print("\nweb_content_cache schema is ready.")


if __name__ == "__main__":
    asyncio.run(main())
