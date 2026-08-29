"""Pre-ingest filter — the primary lever on LLM spend (v2 doc 3.4).

Every episode costs an LLM round trip, so this stage does the job the v1 GLiNER
tier used to do: kill volume cheaply and deterministically before it reaches the
expensive path.
"""

from kg_prefilter.batching import build_episodes, prune_short_episodes, resolve_group_id
from kg_prefilter.filters import PrefilterResult, prefilter_chunks

__all__ = [
    "PrefilterResult",
    "build_episodes",
    "prefilter_chunks",
    "prune_short_episodes",
    "resolve_group_id",
]
