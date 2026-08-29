"""LLM Analysis utilities for GitHub Connector."""

import json
import logging
import re
from typing import Any
import openai
from app.state import AppState
from github.llm_cache import get_cached_response, set_cached_response

logger = logging.getLogger("synapse.github.llm")


def get_llm_clients(state: AppState) -> list[tuple[openai.AsyncOpenAI, str]]:
    """Return a list of (client, model) tuples. Primary first, fallback second."""
    settings = state.settings
    clients = []
    
    if settings.llm_provider == "openai" and settings.openai_api_key:
        clients.append((openai.AsyncOpenAI(api_key=settings.openai_api_key, timeout=15.0), settings.openai_model))
    elif settings.llm_provider == "groq" and settings.groq_api_key:
        clients.append((openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=15.0
        ), settings.groq_model))
    elif settings.llm_provider == "nvidia" and settings.nvidia_api_key:
        clients.append((openai.AsyncOpenAI(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            timeout=15.0
        ), settings.nvidia_model))
        
        if settings.groq_api_key:
            clients.append((openai.AsyncOpenAI(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                timeout=15.0
            ), settings.groq_model))
            
    elif settings.llm_provider == "ollama":
        clients.append((openai.AsyncOpenAI(
            api_key="ollama",
            base_url=settings.ollama_base_url,
            timeout=15.0
        ), settings.ollama_model))
        
    return clients

async def execute_llm_with_fallback(state: AppState, prompt: str, system_prompt: str) -> str:
    clients = get_llm_clients(state)
    if not clients:
        raise ValueError("No LLM clients configured")
        
    for i, (client, model) in enumerate(clients):
        cached = get_cached_response(prompt, system_prompt, model)
        if cached:
            return cached
            
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "seed": 42
        }
        
        # Groq and OpenAI support json_object. NVIDIA's generic chat completions might reject it or require it depending on the exact model.
        if "groq" in str(client.base_url) or "openai" in str(client.base_url) and "nvidia" not in str(client.base_url):
             kwargs["response_format"] = {"type": "json_object"}
             
        try:
            resp = await client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            set_cached_response(prompt, system_prompt, model, text)
            return text
        except Exception as e:
            if i == len(clients) - 1:
                logger.exception("All LLM clients failed")
                raise
            else:
                logger.warning("LLM client %s failed with %s, falling back...", model, str(e))
                continue

async def analyze_file_for_attention(path: str, content: str) -> dict:
    """Analyze file content with regex for critical tags and high complexity."""
    # Check for critical keywords
    critical_pattern = re.compile(r'\b(TODO|FIXME|HACK|SECURITY|BUG)\b', re.IGNORECASE)
    if critical_pattern.search(content):
        return {"required": True, "type": "action_required", "priority": "high", "reason": "Found critical keyword (TODO/FIXME/BUG/etc) in code"}
        
    deep_nesting = re.compile(r'^[ \t]{24,}', re.MULTILINE)
    if len(deep_nesting.findall(content)) > 10:
        return {"required": True, "type": "review_required", "priority": "medium", "reason": "High cyclomatic complexity detected (deep nesting)"}
        
    return {"required": False, "type": "none"}

async def summarize_files_batch(state: AppState, files: list[tuple[str, str]]) -> dict[str, str]:
    """Generate 5-6 line summaries for a batch of files using a single LLM call."""
    if not files:
        return {}
        
    default_res = {path: f"Source code file at {path}." for path, _ in files}

    # Prepare batch content
    batch_text = ""
    for path, content in files:
        truncated = content[:2000] # Limit per file in batch to avoid token overflow
        batch_text += f"--- FILE: {path} ---\n{truncated}\n\n"

    prompt = (
        f"Analyze the following batch of files. For each file, write a detailed technical summary of exactly 5-6 lines covering:\n"
        f"1. Primary purpose and responsibility\n"
        f"2. Key classes/functions\n"
        f"3. Main dependencies\n"
        f"4. Inputs/outputs\n"
        f"5. Architecture role\n"
        f"6. Notable patterns\n\n"
        f"Respond STRICTLY in JSON format as a dictionary mapping the file path to its summary string.\n\n"
        f"Files:\n{batch_text}"
    )

    try:
        system_prompt = "You are a senior software architect. Output valid JSON mapping file paths to summaries."
        text = await execute_llm_with_fallback(state, prompt, system_prompt)
        
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            # Ensure all requested files have a summary
            res = {}
            for path, _ in files:
                val = data.get(path, default_res[path])
                if isinstance(val, dict):
                    val = "\n".join(f"{k}: {v}" for k, v in val.items())
                elif isinstance(val, list):
                    val = "\n".join(str(item) for item in val)
                elif not isinstance(val, str):
                    val = str(val)
                res[path] = val
            return res
        return default_res
    except Exception:
        logger.exception("LLM batch file summarization failed")
        return default_res


async def analyze_issue_for_attention(
    state: AppState,
    title: str,
    body: str,
    comments: list[str],
    repo_files: list[str],
    project_context: str | None = None
) -> dict[str, Any]:
    """Analyze a GitHub issue to see if it represents an active bug/failure and which files it affects."""
    default_res = {"need_attention": False, "affected_files": [], "affected_classes": []}

    comments_str = "\n".join([f"- {c}" for c in comments[:10]])
    files_list_str = "\n".join(repo_files[:200]) # Limit file list size

    context_str = f"\nProject Context / Constraints:\n{project_context}\n" if project_context else ""
    prompt = f"""Analyze the following GitHub issue to determine if it describes an active bug, crash, compilation error, or critical problem that requires immediate developer attention.{context_str}

Issue Title: {title}
Issue Description: {body}
Comments:
{comments_str}

Available files in the repository:
{files_list_str}

Respond STRICTLY in JSON format with the following keys:
- "attention": an object containing:
    - "required": boolean (true if this is a bug, failure, or problem requiring action)
    - "type": string (one of "action_required", "review_required", "error", "none")
    - "priority": string (one of "low", "medium", "high", "critical")
    - "reason": string (short reason for attention)
- "affected_files": list of strings (exact file paths affected)
- "affected_classes": list of strings (class names mentioned)

Example response:
{{
  "attention": {{
    "required": true,
    "type": "error",
    "priority": "high",
    "reason": "Active bug reported with database connection"
  }},
  "affected_files": ["backend/app/db.py"],
  "affected_classes": ["DatabaseConnection"]
}}
"""

    try:
        system_prompt = "You are a code quality analyzer. You must output valid JSON."
        text = await execute_llm_with_fallback(state, prompt, system_prompt)
        
        # Parse JSON
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return {
                "attention": data.get("attention", {"required": False, "type": "none"}),
                "affected_files": [f for f in data.get("affected_files", []) if f in repo_files],
                "affected_classes": [str(c) for c in data.get("affected_classes", [])]
            }
        return default_res
    except Exception:
        logger.exception("LLM issue analysis failed for: %s", title)
        return default_res


async def analyze_pr_for_attention(
    state: AppState,
    title: str,
    body: str,
    diff_files: list[str],
    project_context: str | None = None
) -> dict[str, Any]:
    """Analyze a Pull Request to see if it requires attention (e.g. failing builds, conflicts, or critical review needed)."""
    default_res = {"need_attention": False, "affected_files": []}

    diff_files_str = "\n".join(diff_files)
    context_str = f"\nProject Context / Constraints:\n{project_context}\n" if project_context else ""
    prompt = f"""Analyze the following Pull Request to determine if it requires attention (e.g., mentions failing tests, merge conflicts, build errors, or critical bugs found during code review).{context_str}

PR Title: {title}
PR Description: {body}

Modified files in this PR:
{diff_files_str}

Respond STRICTLY in JSON format with the following keys:
- "attention": an object containing:
    - "required": boolean (true if PR has failing checks, bugs, conflicts)
    - "type": string (one of "action_required", "review_required", "error", "none")
    - "priority": string (one of "low", "medium", "high", "critical")
    - "reason": string (short reason for attention)
- "affected_files": list of strings (subset of modified files containing the issue).

Example response:
{{
  "attention": {{
    "required": true,
    "type": "action_required",
    "priority": "medium",
    "reason": "Merge conflicts need resolution"
  }},
  "affected_files": ["backend/app/main.py"]
}}
"""

    try:
        system_prompt = "You are a build engineering assistant. You must output valid JSON."
        text = await execute_llm_with_fallback(state, prompt, system_prompt)
        
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return {
                "attention": data.get("attention", {"required": False, "type": "none"}),
                "affected_files": [f for f in data.get("affected_files", []) if f in diff_files]
            }
        return default_res
    except Exception:
        logger.exception("LLM PR analysis failed for: %s", title)
        return default_res
