import json
import logging
import os
import hashlib
from typing import Any

logger = logging.getLogger("synapse.github.llm_cache")
CACHE_FILE = ".llm_cache.json"

class LLMCache:
    def __init__(self):
        self.cache = {}
        self.loaded = False

    def _load(self):
        if not self.loaded:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r") as f:
                        self.cache = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load LLM cache: {e}")
            self.loaded = True

    def _save(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.cache, f)
        except Exception as e:
            logger.error(f"Failed to save LLM cache: {e}")

    def _get_key(self, prompt: str, system: str, model: str) -> str:
        s = f"{model}:{system}:{prompt}"
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def get(self, prompt: str, system: str, model: str) -> str | None:
        self._load()
        key = self._get_key(prompt, system, model)
        return self.cache.get(key)

    def set(self, prompt: str, system: str, model: str, response: str):
        self._load()
        key = self._get_key(prompt, system, model)
        self.cache[key] = response
        self._save()

_cache_instance = LLMCache()

def get_cached_response(prompt: str, system: str, model: str) -> str | None:
    return _cache_instance.get(prompt, system, model)

def set_cached_response(prompt: str, system: str, model: str, response: str):
    _cache_instance.set(prompt, system, model, response)
