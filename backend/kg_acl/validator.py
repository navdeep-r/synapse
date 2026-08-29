import logging
from typing import Any

logger = logging.getLogger("synapse.validator")

class CommunityValidator:
    """Validates that a discovered community meets structural and semantic requirements."""
    
    def __init__(self, min_density: float = 0.1, min_entities: int = 2):
        self.min_density = min_density
        self.min_entities = min_entities
        
    def calculate_density(self, entities: set[str], edges: list[dict[str, Any]]) -> float:
        """Calculate structural density."""
        if len(entities) < 2:
            return 0.0
        possible = len(entities) * (len(entities) - 1) / 2
        if possible == 0:
            return 0.0
        internal = sum(
            1 for e in edges
            if e.get("source_uuid") in entities and e.get("target_uuid") in entities
        )
        return internal / possible

    def validate_structure(self, entities: set[str], edges: list[dict[str, Any]]) -> bool:
        """Validate structural density."""
        if len(entities) < self.min_entities:
            return False
        density = self.calculate_density(entities, edges)
        return density >= self.min_density

    async def validate_semantics(self, llm_client: Any, entities: list[dict[str, Any]]) -> bool:
        """Validate semantic cohesion using LLM."""
        if not entities:
            return False
            
        try:
            # We import here to avoid dependency cycles if not used
            from graphiti_core.prompts.models import Message
        except ImportError:
            return True
            
        system_prompt = """You are a knowledge graph validator. 
Analyze the provided list of entities and determine if they form a semantically cohesive community.
Respond ONLY with a JSON object containing a single boolean field "is_cohesive"."""
        
        entity_summaries = "\n".join(f"- {e.get('name', 'Unknown')}: {e.get('summary', '')}" for e in entities[:50]) # cap at 50
        user_prompt = f"Entities:\n{entity_summaries}\n\nAre these entities highly related to each other?"
        
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt)
        ]
        
        try:
            result = await llm_client.generate_response(messages)
            content = result.content if hasattr(result, "content") else str(result)
            # basic clean up in case of markdown block
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()
            
            import json
            parsed = json.loads(content)
            return bool(parsed.get("is_cohesive", True))
        except Exception as e:
            logger.warning(f"Semantic validation failed, defaulting to True: {e}")
            return True
