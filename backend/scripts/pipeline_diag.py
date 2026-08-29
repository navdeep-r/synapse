"""Deep pipeline diagnostics — metrics, receipts, prefilter drops."""
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

    # Pipeline metrics for recent completed jobs
    print("=== Pipeline Metrics (last 5 completed jobs) ===")
    recent_jobs = await db.fetch_all(
        "SELECT job_id, updated_at FROM pipeline_jobs WHERE status='completed' "
        "ORDER BY updated_at DESC LIMIT 5"
    )
    for j in recent_jobs:
        jid = j["job_id"]
        metrics = await db.fetch_all(
            "SELECT metric_name, metric_value FROM pipeline_metrics WHERE job_id=?",
            (jid,)
        )
        mdict = {m["metric_name"]: int(m["metric_value"]) for m in metrics}
        print(f"  {jid[:45]} @ {j['updated_at'][11:19]}")
        print(f"    chunks={mdict.get('chunks',0)}  kept={mdict.get('kept_chunks',0)}  "
              f"nodes={mdict.get('nodes_created',0)}  edges={mdict.get('edges_created',0)}  "
              f"episodes={mdict.get('episodes',0)}")
    print()

    # All episode receipts
    print("=== Episode Receipts (all, newest first) ===")
    all_receipts = await db.fetch_all(
        "SELECT episode_id, status, created_at, committed_at, nodes_created, edges_created, error "
        "FROM episode_receipts ORDER BY created_at DESC LIMIT 15"
    )
    if not all_receipts:
        print("  (none at all)")
    for r in all_receipts:
        print(f"  {r['episode_id'][:35]}  status={r['status']}  "
              f"nodes={r.get('nodes_created',0)}  edges={r.get('edges_created',0)}  "
              f"created={r['created_at'][11:19]}")
        if r.get('error'):
            print(f"    error: {r['error'][:100]}")
    print()

    # Global counters
    print("=== Global Counters ===")
    counters = await db.fetch_all("SELECT name, value FROM counters ORDER BY name")
    for c in counters:
        print(f"  {c['name']}: {c['value']}")
    print()

    # Prefilter drops (last 5)
    print("=== Recent Prefilter Drops ===")
    drops = await db.fetch_all(
        "SELECT outcome, rule, created_at FROM prefilter_drops "
        "ORDER BY created_at DESC LIMIT 10"
    )
    if not drops:
        print("  (none)")
    for d in drops:
        print(f"  [{d['outcome']}]  rule={d['rule']}  at={d['created_at'][11:19]}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
