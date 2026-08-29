"""Test whether llama-3.1-8b supports json_schema response_format on NVIDIA."""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings


SCHEMA = {
    "type": "object",
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fact": {"type": "string"},
                    "source_node_name": {"type": "string"},
                    "target_node_name": {"type": "string"},
                },
                "required": ["name", "fact", "source_node_name", "target_node_name"],
            },
        }
    },
    "required": ["edges"],
}

# Simulate the kind of long prompt Graphiti sends for edge extraction
LONG_PROMPT = """You are an expert graph builder. Extract all relationships from the following episode.

Previous context contains facts about people and organisations.

Episode:
Alice Johnson is the engineering manager for the Platform team at Acme Corp.
She reports to Robert Smith who is VP of Engineering.
Alice manages Priya Raman and Marcus Webb.
The Platform team belongs to Acme Corp.
billing-service depends on auth-library.
billing-service is managed by Priya Raman.
ledger-core is maintained by Marcus Webb.
Acme Corp uses TechCorp for payment processing.

Instructions:
- Extract ALL relationships between entities mentioned above as directed edges.
- Each edge must have a name (relationship type), a fact (one sentence), source_node_name, and target_node_name.
- Be thorough. If A reports to B, create an edge from A to B.
- Output only the JSON object, no explanation."""


async def main() -> None:
    import httpx
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
    model = settings.nvidia_model
    base_url = settings.nvidia_base_url

    print(f"Testing model: {model}")
    print()

    async with httpx.AsyncClient(timeout=60.0) as http:
        # Test 1: json_schema mode
        print("--- Test 1: json_schema response_format ---")
        try:
            r = await http.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": LONG_PROMPT}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "ExtractedEdges",
                            "schema": SCHEMA,
                        },
                    },
                    "max_tokens": 2048,
                    "temperature": 0,
                },
            )
            if r.status_code == 400:
                print(f"FAIL (400) - json_schema not supported: {r.text[:200]}")
            else:
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"] or ""
                parsed = json.loads(content.strip())
                edges = parsed.get("edges", [])
                print(f"PASS - got {len(edges)} edges")
                for e in edges[:3]:
                    print(f"  {e.get('source_node_name')} --[{e.get('name')}]--> {e.get('target_node_name')}")
        except Exception as ex:
            print(f"FAIL: {ex}")

        print()

        # Test 2: json_object mode (current approach)
        print("--- Test 2: json_object response_format (current approach) ---")
        prompt_with_schema = LONG_PROMPT + f"\n\nRespond with a JSON object in the following format:\n\n{json.dumps(SCHEMA, indent=2)}"
        try:
            r = await http.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt_with_schema}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 2048,
                    "temperature": 0,
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
            parsed = json.loads(content.strip())
            edges = parsed.get("edges")
            if isinstance(edges, list):
                print(f"PASS - got {len(edges)} edges")
                for e in edges[:3]:
                    print(f"  {e.get('source_node_name')} --[{e.get('name')}]--> {e.get('target_node_name')}")
            else:
                print(f"FAIL - wrong shape, got keys: {list(parsed.keys())}")
        except Exception as ex:
            print(f"FAIL: {ex}")


if __name__ == "__main__":
    asyncio.run(main())
