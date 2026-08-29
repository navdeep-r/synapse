"""Deterministic entity and relation extraction for the offline stub.

This is the heuristic that stands in for an LLM's extraction pass. It reads
proper nouns, code-style identifiers and a closed vocabulary of relation cues.
It is intentionally conservative: a missed entity is recoverable by ingesting
better text, whereas a hallucinated one pollutes the graph permanently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words that start sentences but are not entities, plus generic nouns the real
# Graphiti prompt also forbids extracting.
STOPWORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "he", "her", "his",
    "i", "if", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "she", "so", "that", "the", "their", "them", "there", "these", "they",
    "this", "those", "to", "us", "we", "what", "when", "where", "which", "who",
    "why", "with", "you", "your", "all", "any", "each", "every", "some",
    "after", "before", "during", "while", "since", "until", "because",
    "however", "therefore", "meanwhile", "also", "then", "now", "today",
    "yesterday", "tomorrow", "here", "how", "not", "no", "yes", "both",
}

GENERIC_NOUNS = {
    "team", "company", "office", "government", "school", "meeting", "event",
    "project", "work", "people", "time", "day", "week", "month", "year",
    "thing", "things", "stuff", "way", "part", "system", "service", "data",
    "information", "report", "document", "page", "note", "email", "message",
}

# Titles that precede a name and should not be captured as part of it.
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir"}

# Roles are attributes of a person, not entities in their own right. Without
# this, "Carol Danvers is the VP of Engineering" yields a "VP" node.
ROLE_TITLES = {
    "vp", "svp", "evp", "ceo", "cto", "cfo", "coo", "cio", "ciso",
    "president", "chief", "officer", "manager", "director", "head", "lead",
    "engineer", "developer", "analyst", "architect", "designer", "intern",
    "consultant", "administrator", "admin", "owner", "maintainer", "steward",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# A run of capitalised words, allowing internal lowercase connectors.
_PROPER_RUN = re.compile(
    r"\b[A-Z][\w'&.-]*(?:\s+(?:of|for|and|the|de|van|von)\s+[A-Z][\w'&.-]*|\s+[A-Z][\w'&.-]*)*"
)
# Code-style identifiers: billing-service, auth_library, PostgreSQL, TechCorp.
_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b")
_CAMEL_CASE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b")
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")

# Closed relation vocabulary carried over from v1. Order matters: the first
# pattern that matches a sentence span wins, so more specific cues come first.
RELATION_CUES: list[tuple[str, re.Pattern[str], bool]] = [
    # (relation_type, pattern, inverted) — inverted flips source and target.
    ("REPORTS_TO", re.compile(r"\breports?\s+(?:directly\s+)?to\b", re.I), False),
    ("REPORTS_TO", re.compile(r"\breporting\s+to\b", re.I), False),
    ("MANAGED_BY", re.compile(r"\b(?:is\s+)?(?:managed|maintained|owned|run)\s+by\b", re.I), False),
    ("MANAGED_BY", re.compile(r"\b(?:manages|maintains|owns|leads)\b", re.I), True),
    ("MANAGED_BY", re.compile(r"\bis\s+the\s+(?:manager|owner|lead|maintainer)\s+of\b", re.I), True),
    ("DEPENDS_ON", re.compile(r"\bdepends?\s+(?:on|upon)\b", re.I), False),
    ("DEPENDS_ON", re.compile(r"\b(?:requires|needs)\b", re.I), False),
    ("DEPENDS_ON", re.compile(r"\bis\s+a\s+dependency\s+of\b", re.I), True),
    ("USES", re.compile(r"\b(?:uses|using|utilises|utilizes)\b", re.I), False),
    ("USES", re.compile(r"\b(?:built|runs|running)\s+on\b", re.I), False),
    ("BELONGS_TO", re.compile(r"\b(?:belongs\s+to|is\s+part\s+of|is\s+a\s+member\s+of)\b", re.I), False),
    ("BELONGS_TO", re.compile(r"\bmember\s+of\b", re.I), False),
    ("BELONGS_TO", re.compile(r"\bworks\s+(?:for|at|in)\b", re.I), False),
]

# Cues that mark a statement as superseding an earlier one.
SUPERSEDING_CUES = re.compile(
    r"\b(now|no longer|as of|effective|has moved|moved to|replaced|instead of|"
    r"transferred|took over|since)\b",
    re.I,
)


@dataclass(frozen=True)
class Mention:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class RelationHit:
    source: str
    target: str
    relation_type: str
    fact: str


_TRIM_CHARS = " \t\n.,;:!?()[]{}\"'"
_TRAILING_WORDS = {"the", "a", "an", "of", "and", "for", "de", "van", "von"}
_LEADING_WORDS = {"the", "a", "an"}
_CONNECTORS = {"of", "for", "and", "the", "de", "van", "von"}
# Beyond this, a connector-joined run is a description rather than a name:
# "Bank of America" is a name, "VP of Engineering for Acme Corp" is not.
_MAX_CONNECTED_TOKENS = 4


def _clean_candidate(raw: str) -> str:
    words = raw.strip(_TRIM_CHARS).split()
    while words and words[0].rstrip(".").casefold() in _HONORIFICS:
        words = words[1:]
    # A leading article is part of the sentence, not the name: "The Platform".
    while words and words[0].strip(_TRIM_CHARS).casefold() in _LEADING_WORDS:
        words = words[1:]
    while words and words[-1].strip(_TRIM_CHARS).casefold() in _TRAILING_WORDS:
        words = words[:-1]
    # Strip again: removing a trailing article can expose punctuation that was
    # interior a moment ago, as in "Robert Smith. The".
    return " ".join(words).strip(_TRIM_CHARS)


def _split_long_run(raw: str) -> list[str]:
    """Break an over-long prepositional run into the names it actually contains.

    Only the head and the tail are kept. The middle of such a run is almost
    always a common noun ("of Engineering"), and inventing a node for it is
    worse than missing one.
    """
    words = raw.split()
    if len(words) <= _MAX_CONNECTED_TOKENS:
        return [raw]
    if not any(w.casefold() in _CONNECTORS for w in words):
        return [raw]

    segments: list[list[str]] = [[]]
    for word in words:
        if word.casefold() in _CONNECTORS:
            segments.append([])
        else:
            segments[-1].append(word)

    populated = [" ".join(s) for s in segments if s]
    if len(populated) < 2:
        return populated or [raw]
    return [populated[0], populated[-1]]


def _is_plausible_entity(name: str) -> bool:
    if len(name) < 2:
        return False
    lowered = name.casefold()
    if lowered in STOPWORDS or lowered in GENERIC_NOUNS:
        return False
    words = [w for w in lowered.split() if w]
    if not words:
        return False
    # A single generic word is never an entity, even capitalised at sentence start.
    if len(words) == 1 and (words[0] in STOPWORDS or words[0] in GENERIC_NOUNS):
        return False
    # A bare role is an attribute of somebody, not a node.
    if len(words) == 1 and words[0] in ROLE_TITLES:
        return False
    # Multi-word runs whose words are all stopwords are sentence fragments.
    if all(w in STOPWORDS for w in words):
        return False
    return True


def find_mentions(text: str) -> list[Mention]:
    """Locate candidate entity mentions with their character offsets.

    Scanning runs per sentence so a capitalised run cannot bridge a full stop:
    on raw text, "Robert Smith. The billing-service" would otherwise match as one
    name, and the resulting node would never match the edge extractor's output.
    """
    found: dict[str, Mention] = {}

    def add(name: str, start: int) -> None:
        cleaned = _clean_candidate(name)
        if not cleaned:
            return
        for part in _split_long_run(cleaned):
            if not _is_plausible_entity(part):
                continue
            # Keep the earliest occurrence of each distinct surface form.
            key = part.casefold()
            if key not in found:
                local = name.find(part)
                offset = start + (local if local >= 0 else 0)
                found[key] = Mention(part, offset, offset + len(part))

    for sentence, base in _sentences(text):
        if _is_heading(sentence):
            continue
        for match in _PROPER_RUN.finditer(sentence):
            add(match.group(0), base + match.start())
        for pattern in (_IDENTIFIER, _CAMEL_CASE, _ACRONYM):
            for match in pattern.finditer(sentence):
                add(match.group(0), base + match.start())

    return sorted(found.values(), key=lambda m: m.start)


def _is_heading(line: str) -> bool:
    """A title line asserts nothing, so extracting from it invents entities.

    Document titles and section headers are short, capitalised and unpunctuated,
    which is exactly the shape of a proper-noun run. Left alone they produce
    nodes like "Engineering Handbook" that no fact ever references.
    """
    stripped = line.strip()
    if not stripped or stripped.endswith((".", "!", "?", ",", ";", ":")):
        return False
    words = stripped.split()
    if not (1 <= len(words) <= 6):
        return False
    # A heading has no verb cue; anything with a relation cue is a real claim.
    if any(pattern.search(stripped) for _type, pattern, _inv in RELATION_CUES):
        return False
    return all(word[:1].isupper() or not word[:1].isalpha() for word in words)


def extract_entities(text: str) -> list[str]:
    """Distinct entity names mentioned in the text, in order of appearance."""
    return [m.name for m in find_mentions(text)]


def _sentences(text: str) -> list[tuple[str, int]]:
    sentences: list[tuple[str, int]] = []
    cursor = 0
    for part in _SENTENCE_SPLIT.split(text):
        if not part:
            continue
        index = text.find(part, cursor)
        if index < 0:
            index = cursor
        sentences.append((part, index))
        cursor = index + len(part)
    return sentences


def extract_relations(text: str, known_entities: list[str] | None = None) -> list[RelationHit]:
    """Find relation cues and bind them to the nearest entity on each side."""
    hits: list[RelationHit] = []
    seen: set[tuple[str, str, str]] = set()

    allowed = {n.casefold() for n in known_entities} if known_entities else None

    for sentence, _offset in _sentences(text):
        mentions = find_mentions(sentence)
        if allowed is not None:
            mentions = [m for m in mentions if m.name.casefold() in allowed]
        if len(mentions) < 2:
            continue

        for relation_type, pattern, inverted in RELATION_CUES:
            for cue in pattern.finditer(sentence):
                before = [m for m in mentions if m.end <= cue.start()]
                after = [m for m in mentions if m.start >= cue.end()]
                if not before or not after:
                    continue

                source, target = before[-1].name, after[0].name
                if inverted:
                    source, target = target, source
                if source.casefold() == target.casefold():
                    continue

                key = (source.casefold(), relation_type, target.casefold())
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    RelationHit(
                        source=source,
                        target=target,
                        relation_type=relation_type,
                        fact=sentence.strip(),
                    )
                )

    return hits


def summarize(name: str, text: str, max_chars: int = 220) -> str:
    """Pick the sentence that best describes an entity."""
    lowered = name.casefold()
    best = ""
    for sentence, _ in _sentences(text):
        if lowered in sentence.casefold():
            best = sentence.strip()
            break
    if not best:
        best = text.strip().split("\n")[0]
    summary = " ".join(best.split())
    return summary[:max_chars].rstrip()
