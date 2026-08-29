"""LLM Telemetry database access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import Database


class TelemetryStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_call(
        self,
        id: str,
        episode_id: str,
        prompt_name: str,
        model_name: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_usd: float,
        input_chars: int,
        nodes_yield: int,
        edges_yield: int,
        error: str | None = None,
    ) -> None:
        """Record a single LLM call's telemetry."""
        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            """
            INSERT INTO llm_telemetry (
                id, episode_id, prompt_name, model_name, latency_ms,
                input_tokens, output_tokens, total_tokens, cost_usd,
                input_chars, nodes_yield, edges_yield, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                episode_id,
                prompt_name,
                model_name,
                latency_ms,
                input_tokens,
                output_tokens,
                total_tokens,
                cost_usd,
                input_chars,
                nodes_yield,
                edges_yield,
                error,
                now,
            ),
        )

    async def get_recent_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get the most recent LLM calls, aggregated by chunk (episode_id)."""
        rows = await self._db.fetch_all(
            """
            SELECT 
                MAX(id) as id,
                episode_id,
                'chunk_extraction' as prompt_name,
                MAX(model_name) as model_name,
                SUM(latency_ms) as latency_ms,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(cost_usd) as cost_usd,
                MAX(input_chars) as input_chars,
                SUM(nodes_yield) as nodes_yield,
                SUM(edges_yield) as edges_yield,
                MAX(error) as error,
                MIN(created_at) as created_at
            FROM llm_telemetry
            GROUP BY episode_id
            ORDER BY created_at DESC 
            LIMIT ?
            """, (limit,)
        )
        return [dict(row) for row in rows]

    async def get_aggregate_stats(self) -> dict[str, Any]:
        """Get aggregate LLM telemetry stats, treating each chunk as 1 call."""
        row = await self._db.fetch_one(
            """
            SELECT 
                COUNT(DISTINCT episode_id) as total_calls,
                SUM(total_tokens) as total_tokens,
                SUM(cost_usd) as total_cost_usd,
                SUM(nodes_yield) as total_nodes_yield,
                SUM(edges_yield) as total_edges_yield,
                SUM(latency_ms) as total_latency_ms
            FROM llm_telemetry
            """
        )
        if not row:
            return {
                "total_calls": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "total_nodes_yield": 0,
                "total_edges_yield": 0,
                "avg_latency_ms": 0.0,
            }
            
        total_calls = int(row["total_calls"] or 0)
        total_latency = float(row["total_latency_ms"] or 0.0)
            
        return {
            "total_calls": total_calls,
            "total_tokens": int(row["total_tokens"] or 0),
            "total_cost_usd": float(row["total_cost_usd"] or 0.0),
            "total_nodes_yield": int(row["total_nodes_yield"] or 0),
            "total_edges_yield": int(row["total_edges_yield"] or 0),
            "avg_latency_ms": (total_latency / total_calls) if total_calls > 0 else 0.0,
        }
