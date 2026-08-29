"""Check GitHub sync run status and repo state."""
import asyncio, sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.db import Database


async def main() -> None:
    db = Database(get_settings().sqlite_path)
    await db.connect()

    runs = await db.fetch_all(
        "SELECT id, repo_id, status, trigger, started_at, completed_at, files_added, commits_processed, error "
        "FROM github_sync_runs ORDER BY started_at DESC LIMIT 10"
    )
    print("=== GitHub Sync Runs ===")
    if not runs:
        print("  (none — sync has never been triggered through the API)")
    for r in runs:
        print(f"  status:     {r['status']}")
        print(f"  trigger:    {r['trigger']}")
        print(f"  started:    {r['started_at']}")
        print(f"  completed:  {r['completed_at']}")
        print(f"  files_added:{r['files_added']}")
        if r.get("error"):
            print(f"  error:      {str(r['error'])[:200]}")
        print()

    repos = await db.fetch_all(
        "SELECT id, full_name, status, last_synced_commit_sha, updated_at FROM github_repositories"
    )
    print("=== GitHub Repositories ===")
    if not repos:
        print("  (none registered)")
    for r in repos:
        sha = r["last_synced_commit_sha"] or "(none)"
        print(f"  {r['full_name']} | status={r['status']} | last_sha={sha[:12]}")

    # Check recent pipeline jobs created by github sync (source_id starts with github:)
    jobs = await db.fetch_all(
        "SELECT j.job_id, j.status, j.updated_at FROM pipeline_jobs j "
        "JOIN documents d ON j.job_id = d.document_id "
        "WHERE d.source_id LIKE 'github:%' ORDER BY j.updated_at DESC LIMIT 5"
    )
    print()
    print("=== Pipeline Jobs from GitHub Source ===")
    if not jobs:
        print("  (none)")
    for j in jobs:
        print(f"  {j['job_id'][:50]} | {j['status']} | {j['updated_at']}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
