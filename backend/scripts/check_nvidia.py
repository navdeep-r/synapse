"""Preflight checks for LLM and Embedding providers (NVIDIA, Groq fallback, EmbeddingGemma).

Run:
    uv run python scripts/check_nvidia.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402


def _mask(secret: str | None) -> str:
    if not secret:
        return "(not set)"
    return f"{secret[:6]}…{secret[-4:]}" if len(secret) > 12 else "…"


async def main() -> int:
    settings = get_settings()

    print("=" * 60)
    print("           SYNAPSE PREFLIGHT PROVIDER DIAGNOSTICS")
    print("=" * 60)
    print(f"  NVIDIA Base URL   : {settings.nvidia_base_url}")
    print(f"  NVIDIA Key        : {_mask(settings.nvidia_api_key)}")
    print(f"  NVIDIA LLM Model  : {settings.nvidia_model}")
    print(f"  NVIDIA Embed Model: {settings.nvidia_embedding_model}")
    print(f"  Groq Fallback Key : {_mask(settings.groq_api_key)}")
    print(f"  Groq LLM Model    : {settings.groq_model}")
    print(f"  HuggingFace Token : {_mask(settings.hf_token)}")
    print(f"  Configured Emb Dim: {settings.embedding_dim}")
    print("=" * 60)
    print()

    import httpx

    failures = 0
    warnings = 0

    # -------------------------------------------------------------
    # 1. NVIDIA LLM Checks
    # -------------------------------------------------------------
    print("[1/4] Checking Primary LLM (NVIDIA)...")
    if not settings.nvidia_api_key:
        print("  FAIL  SYNAPSE_NVIDIA_API_KEY is not set in .env")
        failures += 1
    else:
        headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
        async with httpx.AsyncClient(timeout=45.0) as http:
            # 1a. Basic chat completion
            try:
                response = await http.post(
                    f"{settings.nvidia_base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": settings.nvidia_model,
                        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                        "max_tokens": 256,
                        "temperature": 0,
                    },
                )
                if response.status_code == 401:
                    print("  FAIL  NVIDIA chat 401: Key rejected. Regenerate key at build.nvidia.com.")
                    failures += 1
                elif response.status_code == 404:
                    print(f"  FAIL  NVIDIA chat 404: Model {settings.nvidia_model!r} not found on this account.")
                    failures += 1
                else:
                    response.raise_for_status()
                    message = response.json()["choices"][0]["message"]
                    reply = (message.get("content") or "").strip()
                    print(f"  PASS  NVIDIA chat response: {reply[:60]!r}")
            except Exception as exc:
                print(f"  FAIL  NVIDIA chat completions: {type(exc).__name__}: {exc}")
                failures += 1

            # 1b. Structured JSON output
            try:
                response = await http.post(
                    f"{settings.nvidia_base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": settings.nvidia_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    'Extract entities from "Alice works at Acme Corp." '
                                    'Reply ONLY with JSON: {"entities": [{"name": "Alice", "type": "Person"}]}'
                                ),
                            }
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 256,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                body = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(body)
                print(f"  PASS  NVIDIA structured output: {parsed}")
            except json.JSONDecodeError:
                print("  WARN  NVIDIA structured output: returned non-JSON despite response_format.")
                warnings += 1
            except Exception as exc:
                print(f"  FAIL  NVIDIA structured output: {type(exc).__name__}: {exc}")
                failures += 1

    print()

    # -------------------------------------------------------------
    # 2. Groq Fallback LLM Checks
    # -------------------------------------------------------------
    print("[2/4] Checking Fallback LLM (Groq)...")
    if not settings.groq_api_key:
        print("  INFO  SYNAPSE_GROQ_API_KEY is not set (Fallback LLM disabled).")
    else:
        groq_headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as http:
            try:
                response = await http.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=groq_headers,
                    json={
                        "model": settings.groq_model,
                        "messages": [{"role": "user", "content": "Reply with 'ready'"}],
                        "max_tokens": 128,
                        "temperature": 0,
                    },
                )
                if response.status_code == 401:
                    print("  FAIL  Groq 401: API key rejected.")
                    failures += 1
                else:
                    response.raise_for_status()
                    reply = response.json()["choices"][0]["message"]["content"].strip()
                    print(f"  PASS  Groq chat response: {reply[:60]!r}")
            except Exception as exc:
                print(f"  FAIL  Groq chat completions: {type(exc).__name__}: {exc}")
                failures += 1

    print()

    # -------------------------------------------------------------
    # 3. Active Embedding Model (Google EmbeddingGemma / Local)
    # -------------------------------------------------------------
    print("[3/4] Checking Local Embedding Model (Google EmbeddingGemma)...")
    try:
        from sentence_transformers import SentenceTransformer
        if settings.hf_token:
            os.environ["HF_TOKEN"] = settings.hf_token

        print("  ... loading 'google/embeddinggemma-300m' via sentence-transformers ...")
        embed_model = SentenceTransformer("google/embeddinggemma-300m", token=settings.hf_token)
        test_emb = embed_model.encode(["Alice works at Acme Corp."])
        dim = len(test_emb[0])
        print(f"  PASS  EmbeddingGemma loaded successfully! Vector dim: {dim}")
        if dim != settings.embedding_dim:
            print(f"  WARN  Configured SYNAPSE_EMBEDDING_DIM={settings.embedding_dim}, but model output dim={dim}.")
            warnings += 1
    except Exception as exc:
        exc_str = str(exc)
        if "GatedRepoError" in type(exc).__name__ or "403" in exc_str or "restricted" in exc_str:
            print("  FAIL  Gated Model Access Error (403 Forbidden).")
            print("        Google's 'embeddinggemma-300m' requires accepting their license:")
            print("        1. Go to: https://huggingface.co/google/embeddinggemma-300m")
            print("        2. Click 'Acknowledge license' / 'Request access' while logged in.")
            print("        3. Ensure your token in SYNAPSE_HF_TOKEN has Read permissions.")
        elif "401" in exc_str or "Unauthorized" in exc_str:
            print("  FAIL  Authentication Error (401 Unauthorized).")
            print("        Please provide a valid HuggingFace Token in SYNAPSE_HF_TOKEN in .env.")
        else:
            print(f"  FAIL  EmbeddingGemma loading error: {type(exc).__name__}: {exc}")
        failures += 1

    print()

    # -------------------------------------------------------------
    # 4. NVIDIA Embeddings Endpoint (Diagnostic)
    # -------------------------------------------------------------
    print("[4/4] Checking NVIDIA Embeddings Endpoint (Diagnostic)...")
    if not settings.nvidia_api_key:
        print("  INFO  NVIDIA API key not set, skipping NVIDIA embeddings check.")
    else:
        headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
        async with httpx.AsyncClient(timeout=45.0) as http:
            try:
                response = await http.post(
                    f"{settings.nvidia_base_url}/embeddings",
                    headers=headers,
                    json={
                        "model": settings.nvidia_embedding_model,
                        "input": ["Alice works at Acme Corp."],
                        "encoding_format": "float",
                    },
                )
                if response.status_code == 400 and "input_type" in response.text:
                    print(f"  WARN  NVIDIA embed: {settings.nvidia_embedding_model!r} requires asymmetric 'input_type' hint.")
                    warnings += 1
                elif response.status_code >= 400:
                    print(f"  WARN  NVIDIA embed HTTP {response.status_code}: {response.text[:120]}")
                    warnings += 1
                else:
                    vector = response.json()["data"][0]["embedding"]
                    print(f"  PASS  NVIDIA embeddings endpoint reachable. Dim: {len(vector)}")
            except httpx.TimeoutException:
                print("  WARN  NVIDIA embeddings timed out (>45s). (Good thing we are using local EmbeddingGemma!)")
                warnings += 1
            except Exception as exc:
                print(f"  WARN  NVIDIA embeddings error: {type(exc).__name__}: {exc}")
                warnings += 1

    print()
    print("=" * 60)
    if failures == 0:
        print(f"ALL REQUIRED CHECKS PASSED ({warnings} warning(s)).")
        return 0
    else:
        print(f"{failures} CHECK(S) FAILED ({warnings} warning(s)). See details above.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
