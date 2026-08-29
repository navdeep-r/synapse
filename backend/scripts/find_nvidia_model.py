"""Test multiple NVIDIA models for json_object structured output compatibility.

Tries each candidate against the exact kind of prompt Graphiti uses.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings

# Candidates: fast models available on NVIDIA's catalog
# Criteria: must honour json_object, must have >4096 context
CANDIDATES = [
    "openai/gpt-oss-120b",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "extracted_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["name", "entity_type", "summary"],
            },
        }
    },
    "required": ["extracted_entities"],
}

PROMPT = (
    'Extract entities from: "Alice Johnson is the engineering manager for Acme Corp. '
    'She reports to Robert Smith who is VP of Engineering."\n\n'
    f"Respond ONLY with a JSON object matching this schema:\n\n{json.dumps(SCHEMA, indent=2)}"
)


async def test_model(http, headers: dict, base_url: str, model: str) -> tuple[str, bool, str]:
    try:
        r = await http.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "response_format": {"type": "json_object"},
                "max_tokens": 1024,
                "temperature": 0,
            },
            timeout=30.0,
        )
        if r.status_code == 404:
            return model, False, "404 — not provisioned on this account"
        if r.status_code == 401:
            return model, False, "401 — bad key"
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        parsed = json.loads(content.strip())
        entities = parsed.get("extracted_entities")
        if isinstance(entities, list) and len(entities) > 0 and "name" in entities[0]:
            names = [e.get("name") for e in entities]
            return model, True, f"entities={names}"
        else:
            return model, False, f"wrong shape: {str(parsed)[:120]}"
    except json.JSONDecodeError:
        return model, False, f"non-JSON response"
    except Exception as e:
        return model, False, f"{type(e).__name__}: {str(e)[:120]}"


async def main() -> None:
    import httpx
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
    base_url = settings.nvidia_base_url

    print(f"Testing {len(CANDIDATES)} models against {base_url}\n")
    print(f"{'Model':<50} {'Result':<6} Detail")
    print("-" * 100)

    async with httpx.AsyncClient(timeout=40.0) as http:
        for model in CANDIDATES:
            m, ok, detail = await test_model(http, headers, base_url, model)
            status = "PASS" if ok else "FAIL"
            print(f"{m:<50} {status}  {detail}")

    print("\nSet SYNAPSE_NVIDIA_MODEL to a PASS model in .env")


if __name__ == "__main__":
    asyncio.run(main())
