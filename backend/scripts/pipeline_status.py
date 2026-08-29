"""Detailed pipeline status — pending, processing, recent jobs."""
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

    # All jobs by status
    summary = await db.fetch_all(
        "SELECT status, COUNT(*) as cnt FROM pipeline_jobs GROUP BY status"
    )
    print("=== Job Status Summary ===")
    for s in summary:
        print(f"  {s['status']}: {s['cnt']}")
    print()

    # Pending / processing jobs
    stuck = await db.fetch_all(
        "SELECT job_id, status, created_at, updated_at, error "
        "FROM pipeline_jobs WHERE status IN ('pending', 'processing') "
        "ORDER BY created_at DESC LIMIT 10"
    )
    print("=== Pending / Processing Jobs ===")
    if not stuck:
        print("  (none)")
    for j in stuck:
        print(f"  job_id:     {j['job_id'][:50]}")
        print(f"  status:     {j['status']}")
        print(f"  created_at: {j['created_at']}")
        print(f"  updated_at: {j['updated_at']}")
        print()

    # Most recent jobs (any status)
    recent = await db.fetch_all(
        "SELECT job_id, status, created_at, updated_at, error "
        "FROM pipeline_jobs ORDER BY updated_at DESC LIMIT 5"
    )
    print("=== Most Recent Jobs ===")
    for j in recent:
        print(f"  job_id:     {j['job_id'][:50]}")
        print(f"  status:     {j['status']}")
        print(f"  updated_at: {j['updated_at']}")
        if j.get('error'):
            print(f"  error:      {j['error'][:120]}")
        print()

    # Recent receipts (episodes)
    receipts = await db.fetch_all(
        "SELECT episode_id, status, created_at, committed_at, error "
        "FROM episode_receipts ORDER BY created_at DESC LIMIT 5"
    )
    print("=== Recent Episode Receipts ===")
    if not receipts:
        print("  (none)")
    for r in receipts:
        print(f"  episode_id:   {r['episode_id'][:40]}")
        print(f"  status:       {r['status']}")
        print(f"  created_at:   {r['created_at']}")
        if r.get('error'):
            print(f"  error:        {r['error'][:120]}")
        print()

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
