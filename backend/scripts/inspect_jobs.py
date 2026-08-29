"""Detailed inspection of pipeline_jobs and errors."""
import asyncio, sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.db import Database


async def main() -> None:
    db = Database(get_settings().sqlite_path)
    await db.connect()

    rows = await db.fetch_all(
        "SELECT job_id, status, created_at, updated_at, error FROM pipeline_jobs ORDER BY updated_at DESC"
    )
    
    print(f"Total jobs: {len(rows)}")
    print("=== Jobs status and errors (newest updated first) ===")
    for r in rows[:15]:
        err = r.get("error")
        err_str = f" | Error: {err[:120]}" if err else ""
        print(f"  Job: {r['job_id'][:50]:<50} | Status: {r['status']:<10} | Updated: {r['updated_at']}{err_str}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
