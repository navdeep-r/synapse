"""Check detailed pipeline progress."""
import asyncio, sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.db import Database


async def main() -> None:
    db = Database(get_settings().sqlite_path)
    await db.connect()

    jobs = await db.fetch_all(
        "SELECT status, COUNT(*) as cnt FROM pipeline_jobs GROUP BY status"
    )
    print("=== Pipeline Jobs by Status ===")
    if not jobs:
        print("  (no jobs in pipeline)")
    for j in jobs:
        print(f"  {j['status']}: {j['cnt']}")

    processing = await db.fetch_all(
        "SELECT job_id, updated_at FROM pipeline_jobs WHERE status='processing' LIMIT 5"
    )
    if processing:
        print("\n=== Currently Processing ===")
        for p in processing:
            print(f"  {p['job_id'][:80]}")

    completed = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM pipeline_jobs WHERE status='completed'"
    )
    total = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM pipeline_jobs"
    )
    
    comp_cnt = completed["cnt"] if completed else 0
    tot_cnt = total["cnt"] if total else 0

    if tot_cnt > 0:
        percent = (comp_cnt / tot_cnt) * 100
        print(f"\nProgress: {comp_cnt}/{tot_cnt} jobs completed ({percent:.1f}%)")
    else:
        print("\nProgress: 0/0 jobs")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
