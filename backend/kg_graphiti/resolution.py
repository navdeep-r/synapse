"""Alias-aware entity matching — the shared core behind resolution (R1).

Why this module exists
----------------------
With the stub LLM, entity resolution has two halves and they must agree:

* the **embedder** decides which existing nodes are even considered (recall);
* the **`NodeDuplicate` handler** decides whether a candidate is the same entity
  (precision).

Improving only the embedder would surface the right candidates and then reject
them, so both sides import this one module.

Design note on the merge rule
-----------------------------
A merge requires a *structural* match: identical normalized form, a nickname- or
initial-equivalent person name, or a recognised abbreviation. Jaro-Winkler feeds
the confidence score and candidate ranking but never triggers a merge on its own.

That restraint is deliberate. `Tech Corp` and `Tech Corps Ltd` score ~0.97 on
Jaro-Winkler yet are different companies; a similarity threshold high enough to
separate them would also reject genuine aliases. The trade is that pure typos
(`Micrsoft` / `Microsoft`) are left as distinct nodes for a stewardto merge, which
is the safer failure direction for an enterprise graph.

Known ceiling: this is orthographic, not semantic. It will not connect
`the Redmond company` to `Microsoft`. Switch SYNAPSE_LLM_PROVIDER to openai or
ollama for semantic resolution.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Corporate suffixes stripped before comparison so "Tech Corp Inc." == "TechCorp".
LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "llp", "lp", "plc", "gmbh", "ag", "sa", "nv", "bv",
    "ab", "oy", "as", "pty", "pte", "srl", "spa", "kk", "holdings", "group",
}

# Deliberately small and explicit. Each pair is a claim about English given names.
_NICKNAME_GROUPS = [
    {"robert", "bob", "rob", "bobby"},
    {"william", "bill", "will", "billy"},
    {"richard", "rick", "dick", "richie"},
    {"james", "jim", "jimmy"},
    {"john", "jon", "johnny"},
    {"michael", "mike", "mikey"},
    {"christopher", "chris"},
    {"elizabeth", "liz", "beth", "betsy", "eliza"},
    {"katherine", "catherine", "kate", "katie", "kathy", "cathy"},
    {"margaret", "maggie", "meg", "peggy"},
    {"jennifer", "jen", "jenny"},
    {"patricia", "pat", "patty", "trish"},
    {"thomas", "tom", "tommy"},
    {"charles", "charlie", "chuck"},
    {"daniel", "dan", "danny"},
    {"matthew", "matt"},
    {"anthony", "tony"},
    {"joseph", "joe", "joey"},
    {"steven", "stephen", "steve"},
    {"edward", "ed", "eddie", "ted"},
    {"alexander", "alex", "sasha"},
    {"nicholas", "nick"},
    {"benjamin", "ben"},
    {"samuel", "sam", "sammy"},
    {"andrew", "andy", "drew"},
    {"jonathan", "jonny"},
    {"deborah", "debbie", "deb"},
    {"susan", "sue", "susie"},
    {"rebecca", "becky"},
    {"alexandra", "lexi"},
]

# Canonical form for every alias in a group (the first, longest-form member).
NICKNAME_CANONICAL: dict[str, str] = {}
for _group in _NICKNAME_GROUPS:
    _canonical = sorted(_group, key=lambda n: (-len(n), n))[0]
    for _alias in _group:
        NICKNAME_CANONICAL[_alias] = _canonical

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
_SUFFIX_TITLES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(value: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    text = strip_accents(value).casefold()
    text = text.replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def tokens(value: str) -> list[str]:
    return [t for t in normalize(value).split(" ") if t]


def canonical_tokens(value: str) -> list[str]:
    """Tokens with honorifics and generational suffixes removed, nicknames folded.

    Legal suffixes are deliberately kept here — `Corp` is dropped positionally by
    `canonical_keys`, not globally, because removing it everywhere would reduce
    `Tech Corp Inc.` to `tech` and it could then never equal `TechCorp`.
    """
    result: list[str] = []
    for token in tokens(value):
        if token in _TITLES or token in _SUFFIX_TITLES:
            continue
        result.append(NICKNAME_CANONICAL.get(token, token))
    return result or tokens(value)


def canonical_key(value: str) -> str:
    """Whitespace-free canonical form, used for similarity scoring and display."""
    return "".join(canonical_tokens(value))


def canonical_keys(value: str) -> set[str]:
    """All whitespace-free forms this name can legitimately take.

    Trailing legal suffixes are peeled one at a time, so `Tech Corp Inc.` yields
    {techcorpinc, techcorp, tech} and meets `TechCorp` at `techcorp`, while
    `Tech Corps Ltd` yields {techcorpsltd, techcorps} and never meets it.
    """
    parts = canonical_tokens(value)
    if not parts:
        return set()

    variants = {"".join(parts)}
    while len(parts) > 1 and parts[-1] in LEGAL_SUFFIXES:
        parts = parts[:-1]
        variants.add("".join(parts))
    return variants


def jaro_winkler(left: str, right: str, prefix_weight: float = 0.1) -> float:
    """Standard Jaro-Winkler. Used for ranking and confidence, never as the sole merge trigger."""
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    match_window = max(len(left), len(right)) // 2 - 1
    match_window = max(match_window, 0)

    left_flags = [False] * len(left)
    right_flags = [False] * len(right)
    matches = 0

    for i, ch in enumerate(left):
        start = max(0, i - match_window)
        end = min(i + match_window + 1, len(right))
        for j in range(start, end):
            if not right_flags[j] and right[j] == ch:
                left_flags[i] = right_flags[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i, flagged in enumerate(left_flags):
        if not flagged:
            continue
        while not right_flags[k]:
            k += 1
        if left[i] != right[k]:
            transpositions += 1
        k += 1
    transpositions //= 2

    jaro = (
        matches / len(left) + matches / len(right) + (matches - transpositions) / matches
    ) / 3

    prefix = 0
    # Deliberately stops at the shorter string: this measures common prefix length.
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
        if prefix == 4:
            break

    return jaro + prefix * prefix_weight * (1 - jaro)


def token_set_ratio(left: str, right: str) -> float:
    """Jaccard overlap of canonical token sets."""
    a, b = set(canonical_tokens(left)), set(canonical_tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_initialism(short: str, long_form: str) -> bool:
    """`IBM` matches `International Business Machines`."""
    short_tokens, long_tokens = tokens(short), tokens(long_form)
    if len(short_tokens) != 1 or len(long_tokens) < 2:
        return False
    letters = short_tokens[0]
    if len(letters) < 2 or len(letters) != len(long_tokens):
        return False
    return all(word.startswith(ch) for ch, word in zip(letters, long_tokens, strict=True))


def _is_abbreviation(short: str, long_form: str, *, short_raw: str) -> bool:
    """`MSFT` matches `Microsoft`: an uppercase ticker-like subsequence of one word."""
    short_tokens, long_tokens = tokens(short), tokens(long_form)
    if len(short_tokens) != 1 or len(long_tokens) != 1:
        return False
    abbrev, word = short_tokens[0], long_tokens[0]
    if len(abbrev) < 3 or len(abbrev) >= len(word):
        return False
    # Require the raw form to be written as an all-caps token, which is what
    # distinguishes a ticker/acronym from an ordinary truncation.
    if not short_raw.strip().isupper():
        return False
    if abbrev[0] != word[0]:
        return False
    index = 0
    for ch in abbrev:
        index = word.find(ch, index)
        if index < 0:
            return False
        index += 1
    return True


@dataclass(frozen=True)
class MatchVerdict:
    is_match: bool
    score: float
    reason: str


def compare(left: str, right: str) -> MatchVerdict:
    """Decide whether two entity names denote the same entity."""
    if not left or not right:
        return MatchVerdict(False, 0.0, "empty")

    left_norm, right_norm = normalize(left), normalize(right)
    if left_norm == right_norm:
        return MatchVerdict(True, 1.0, "exact")

    shared = canonical_keys(left) & canonical_keys(right)
    if shared:
        # Covers legal-suffix and spacing variance, plus nickname-only differences.
        raw = tokens(left) + tokens(right)
        reason = "nickname" if any(NICKNAME_CANONICAL.get(t, t) != t for t in raw) else "normalized"
        return MatchVerdict(True, 0.97, reason)

    if _is_initialism(left, right) or _is_initialism(right, left):
        return MatchVerdict(True, 0.93, "initialism")

    if _is_abbreviation(left, right, short_raw=left) or _is_abbreviation(
        right, left, short_raw=right
    ):
        return MatchVerdict(True, 0.9, "abbreviation")

    # No structural rule fired. Report similarity for ranking and for the merge
    # queue, but do not merge.
    score = max(
        jaro_winkler(canonical_key(left), canonical_key(right)),
        token_set_ratio(left, right),
    )
    return MatchVerdict(False, round(score, 4), "below_threshold")


def similarity(left: str, right: str) -> float:
    """Ranking score in [0, 1]. Structural matches always outrank fuzzy ones."""
    return compare(left, right).score


# Entities scoring at or above this without a structural match are worth a human
# look: close enough to be suspicious, not close enough to merge automatically.
REVIEW_THRESHOLD = 0.86
