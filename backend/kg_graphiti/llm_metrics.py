"""Count LLM calls for providers that are not the stub.

Only `StubLLMClient` incremented `llm_calls`, so every cost and yield metric the
Observability screen shows — calls per episode, fallback rate, latency — read zero
the moment a real provider was configured. Zero is indistinguishable from "nothing
ran", which is the worst possible reading for a panel whose job is to tell you
whether extraction is working and what it costs.

`generate_response` is the public entry point Graphiti calls once per logical
request; its internal retries happen below this line, so the count is requests
issued rather than HTTP attempts made.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from collections import Counter
from typing import Any

from app.db import Database

METRIC_CALLS = "llm_calls"
METRIC_ERRORS = "llm_errors"
METRIC_LATENCY = "llm_latency_ms"

logger = logging.getLogger("synapse.llm_metrics")

current_tokens: contextvars.ContextVar[tuple[int, int]] = contextvars.ContextVar(
    "current_tokens", default=(0, 0)
)


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Approximate pricing per 1M tokens."""
    pricing = {
        "gpt-4o": (2.5, 10.0),
        "gpt-4o-mini": (0.150, 0.600),
        "gpt-4-turbo": (10.0, 30.0),
        "gpt-3.5-turbo": (0.5, 1.5),
        "claude-3-5-sonnet": (3.0, 15.0),
        "gemini-1.5-pro": (3.5, 10.5),
        "gemini-1.5-flash": (0.075, 0.3),
    }
    model_lower = str(model_name).lower()
    for key, (in_price, out_price) in pricing.items():
        if key in model_lower:
            return (input_tokens * in_price / 1_000_000) + (output_tokens * out_price / 1_000_000)
            
    # Default fallback pricing (similar to gpt-4o-mini: $0.15 input, $0.60 output per 1M tokens)
    # to ensure even extremely small costs for unrecognized models are recorded.
    in_price, out_price = (0.15, 0.60)
    return (input_tokens * in_price / 1_000_000) + (output_tokens * out_price / 1_000_000)


def instrument(client: Any, counters: Counter, db: Database | None = None) -> Any:
    """Wrap the client's `generate_response` in place and return it.

    Patching the bound method rather than wrapping the object in a proxy keeps the
    instance's real class, so Graphiti's `isinstance(..., LLMClient)` checks and any
    attribute it reads off the client still work.
    """
    # 1. Monkey-patch the client to intercept raw token usage
    # Graphiti's OpenAIGenericClient (used for NVIDIA/Ollama) bypasses its own token_tracker,
    # so we intercept the underlying AsyncOpenAI client directly.
    if hasattr(client, "client") and hasattr(client.client, "chat") and hasattr(client.client.chat, "completions"):
        original_create = client.client.chat.completions.create
        
        async def wrapped_create(*args: Any, **kwargs: Any) -> Any:
            response = await original_create(*args, **kwargs)
            if hasattr(response, "usage") and response.usage:
                in_t = getattr(response.usage, "prompt_tokens", 0) or 0
                out_t = getattr(response.usage, "completion_tokens", 0) or 0
                curr_in, curr_out = current_tokens.get()
                current_tokens.set((curr_in + in_t, curr_out + out_t))
            return response
            
        client.client.chat.completions.create = wrapped_create
        
    elif hasattr(client, "token_tracker") and hasattr(client.token_tracker, "record"):
        original_record = client.token_tracker.record
        
        def wrapped_record(prompt_name: str | None, in_tokens: int, out_tokens: int) -> None:
            curr_in, curr_out = current_tokens.get()
            current_tokens.set((curr_in + in_tokens, curr_out + out_tokens))
            original_record(prompt_name, in_tokens, out_tokens)
            
        client.token_tracker.record = wrapped_record

    # 2. Wrap generate_response
    original = client.generate_response

    async def counted(*args: Any, **kwargs: Any) -> Any:
        counters[METRIC_CALLS] += 1
        started = time.perf_counter()
        
        # Reset context var just in case
        current_tokens.set((0, 0))
        
        error_msg = None
        result = None
        
        try:
            result = await original(*args, **kwargs)
            return result
        except Exception as e:
            counters[METRIC_ERRORS] += 1
            error_msg = str(e)
            raise
        finally:
            latency = int((time.perf_counter() - started) * 1000)
            counters[METRIC_LATENCY] += latency
            
            # Extract tokens recorded by token_tracker
            in_tokens, out_tokens = current_tokens.get()
            
            # Extract prompt name and model
            prompt_name = kwargs.get("prompt_name", "unknown")
            model_name = getattr(client, "model", None) or "unknown"
            if hasattr(model_name, "value"):
                model_name = model_name.value
                
            # If the user specified nvidia but didn't set SYNAPSE_NVIDIA_MODEL,
            # it defaults to openai/gpt-oss-20b. Let's make the pricing match NVIDIA's actual models
            # if we see a weird generic name.
            if model_name == "openai/gpt-oss-20b":
                model_name = "nvidia/gpt-oss-20b"
                
            # Extract data-to-token ratio metrics (chars)
            input_chars = 0
            messages = kwargs.get("messages") or (args[0] if len(args) > 0 else None)
            if messages:
                for msg in messages:
                    if hasattr(msg, "content"):
                        input_chars += len(str(msg.content))
                        
            # Extract yield
            nodes_yield = 0
            edges_yield = 0
            if isinstance(result, dict):
                # Different prompts return different root keys for entities/nodes
                if "extracted_entities" in result and isinstance(result["extracted_entities"], list):
                    nodes_yield = len(result["extracted_entities"])
                elif "nodes" in result and isinstance(result["nodes"], list):
                    nodes_yield = len(result["nodes"])
                    
                if "edges" in result and isinstance(result["edges"], list):
                    edges_yield = len(result["edges"])
                    
            cost = estimate_cost(model_name, in_tokens, out_tokens)
            total_tokens = in_tokens + out_tokens
            
            # Save telemetry asynchronously if db exists
            if db:
                from kg_observability.telemetry import TelemetryStore
                from kg_graphiti.service import current_episode_id
                
                episode_id = current_episode_id.get()
                if episode_id:
                    store = TelemetryStore(db)
                    call_id = str(uuid.uuid4())
                    
                    # Fire and forget the DB insert
                    async def _save() -> None:
                        try:
                            await store.record_call(
                                id=call_id,
                                episode_id=episode_id,
                                prompt_name=prompt_name,
                                model_name=str(model_name),
                                latency_ms=latency,
                                input_tokens=in_tokens,
                                output_tokens=out_tokens,
                                total_tokens=total_tokens,
                                cost_usd=cost,
                                input_chars=input_chars,
                                nodes_yield=nodes_yield,
                                edges_yield=edges_yield,
                                error=error_msg,
                            )
                        except Exception:
                            logger.exception("Failed to save LLM telemetry")
                    
                    asyncio.create_task(_save())

    client.generate_response = counted
    return client
