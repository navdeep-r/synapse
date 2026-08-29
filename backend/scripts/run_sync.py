"""Run GitHub Sync synchronously for debugging."""
import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.state import AppState
from github.connector import GitHubSyncJob


async def main() -> None:
    # Set logging to debug/info so we can see what's happening
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("run_sync")

    settings = get_settings()
    state = await AppState.create(settings)
    await state.graph.start()

    # Clear old chunk hashes first so it doesn't get skipped/deduplicated if we want a fresh run
    # (Optional, but useful to ensure it actually pulls files)
    print("Clearing pipeline database to ensure fresh processing...")
    # Import our clear_pipeline script logic
    import sqlite3
    PIPELINE_TABLES = [
        "chunks",
        "prefilter_drops",
        "pipeline_jobs",
        "pipeline_metrics",
        "episode_receipts",
        "dead_letters",
        "counters",
        "documents",
    ]
    conn = sqlite3.connect("var/synapse.db", timeout=5.0)
    cursor = conn.cursor()
    for table in PIPELINE_TABLES:
        try:
            cursor.execute(f"DELETE FROM {table};")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

    # Get the repo
    repo = await state.db.fetch_one("SELECT id, full_name FROM github_repositories LIMIT 1")
    if not repo:
        print("No GitHub repository registered in database.")
        await state.close()
        return

    repo_id = repo["id"]
    print(f"Starting GitHub sync for {repo['full_name']} (ID: {repo_id})...")

    # Reset repository status to READY so we can run
    await state.db.execute("UPDATE github_repositories SET status = 'READY', last_synced_commit_sha = NULL WHERE id = ?", (repo_id,))
    # Clear any active sync runs
    await state.db.execute("DELETE FROM github_sync_runs WHERE repo_id = ?", (repo_id,))

    job = GitHubSyncJob(state, repo_id)
    await job.run()

    # Get final status of the sync run
    run = await state.db.fetch_one(
        "SELECT status, error, files_added, commits_processed FROM github_sync_runs WHERE repo_id = ? ORDER BY started_at DESC LIMIT 1",
        (repo_id,)
    )
    if run:
        print("\n=== Sync Run Result ===")
        print(f"Status:            {run['status']}")
        print(f"Files Added:       {run['files_added']}")
        print(f"Commits Processed: {run['commits_processed']}")
        if run['error']:
            print(f"Error:             {run['error']}")

    await state.close()


if __name__ == "__main__":
    asyncio.run(main())
