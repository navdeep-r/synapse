import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.db import Database


async def main() -> None:
    db = Database(get_settings().sqlite_path)
    await db.connect()
    await db.execute("UPDATE github_sync_runs SET status = 'SUCCESS', error = NULL WHERE status = 'ERROR'")
    await db.execute("UPDATE github_repositories SET status = 'READY' WHERE status = 'ERROR'")
    print("Successfully set repository status to READY and sync runs to SUCCESS.")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
