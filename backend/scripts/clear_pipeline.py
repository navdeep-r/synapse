"""Clear pipeline data (chunks, jobs, episodes) without touching auth/RBAC tables.

Use this after clearing Neo4j, to allow documents to be re-processed
without the prefilter marking them all as duplicates.
"""
import sqlite3

# Tables to clear: pipeline state, NOT auth/RBAC
PIPELINE_TABLES = [
    "chunks",
    "prefilter_drops",
    "pipeline_jobs",
    "pipeline_metrics",
    "episode_receipts",
    "dead_letters",
    "counters",
    "documents",
    "source_activity",
]

# Reset source document counts too
SOURCE_COUNT_SQL = "UPDATE sources SET document_count = 0, last_activity_at = NULL"

try:
    conn = sqlite3.connect("var/synapse.db", timeout=5.0)
    cursor = conn.cursor()
    for table in PIPELINE_TABLES:
        try:
            cursor.execute(f"DELETE FROM {table};")
            print(f"  Cleared: {table}")
        except sqlite3.OperationalError as e:
            print(f"  Skipped: {table} ({e})")
    try:
        cursor.execute(SOURCE_COUNT_SQL)
        print("  Reset source document counts")
    except sqlite3.OperationalError as e:
        print(f"  Skipped source reset: {e}")
    conn.commit()
    conn.close()
    print("\nPipeline data cleared. Auth/RBAC tables preserved.")
    print("You can now re-upload documents or run: uv run scripts/seed.py")
except Exception as e:
    print("Failed:", e)
