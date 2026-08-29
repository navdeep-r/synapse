"""Check pipeline failures in the database — with timestamps."""
from __future__ import annotations
import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.db import Database


async def main() -> None:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    await db.connect()

    # Failed pipeline jobs
    jobs = await db.fetch_all(
        "SELECT job_id, status, error, created_at, updated_at "
        "FROM pipeline_jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 10"
    )
    print("=== Failed Pipeline Jobs ===")
    if not jobs:
        print("  (none)")
    for j in jobs:
        jid = j["job_id"][:50] if j["job_id"] else ""
        print(f"  job_id:     {jid}")
        print(f"  updated_at: {j['updated_at']}")
        print(f"  error:      {j['error']}")
        print()

    # Dead letters with timestamps
    letters = await db.fetch_all(
        "SELECT topic, stage, error_type, error, created_at FROM dead_letters ORDER BY created_at DESC LIMIT 10"
    )
    print("=== Dead Letters (newest first) ===")
    if not letters:
        print("  (none)")
    for l in letters:
        print(f"  created_at: {l['created_at']}")
        print(f"  stage:      {l['stage']}")
        print(f"  error:      {l['error'][:200]}")
        print()

    # All jobs summary
    summary = await db.fetch_all(
        "SELECT status, COUNT(*) as cnt FROM pipeline_jobs GROUP BY status"
    )
    print("=== Pipeline Jobs Summary ===")
    for s in summary:
        print(f"  {s['status']}: {s['cnt']}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
