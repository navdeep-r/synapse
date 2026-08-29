import asyncio
import uuid
import sys
from datetime import datetime, UTC
from app.db import Database

async def main():
    if len(sys.argv) < 5:
        print("Usage: python -m scripts.add_github_repo <installation_id> <owner> <repo_name> <github_repo_id>")
        sys.exit(1)

    installation_id = sys.argv[1]
    owner = sys.argv[2]
    name = sys.argv[3]
    github_repo_id = sys.argv[4]

    # Removed tenant logic

    db = Database("sqlite+aiosqlite:///data/synapse.db")
    await db.connect()
    
    repo_uuid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    default_branch = "main"

    try:
        await db.execute(
            """
            INSERT INTO github_repositories (
                id, installation_id, owner, name, github_repo_id,
                full_name, default_branch, poll_interval_hours, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_uuid, installation_id, owner, name, github_repo_id, f"{owner}/{name}", default_branch, 1, now, now)
        )
        print(f"Successfully added repository {owner}/{name} (ID: {repo_uuid}) to the database.")
        print(f"You can now go to the Synapse Console -> Data Sources -> GitHub to see it and click 'Sync Now'!")
    except Exception as e:
        print(f"Failed to add repository: {e}")
    finally:
        await db.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
