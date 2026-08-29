"""Resume-aware text shaping before chunking / extraction.

Resumes are a hostile input for open extraction: contact lines become entities,
PDF reading order often puts the candidate name at the bottom, and section
headers ("EXPERIENCE", "TECHNICAL SKILLS") get promoted to nodes. None of that
is a model failure — it's the shape of the document.

This normaliser runs only when the file looks like a resume. It does three
things, each of which removes a specific class of garbage from the graph:

1. Promote the candidate name to the top so every episode carries the subject.
2. Replace raw emails / phone numbers with labelled prose (still readable, no
   longer looks like a named entity).
3. Prefix a one-line extraction brief so the LLM knows people are Employees,
   employers are Organizations, projects/tech are Products.
"""

from __future__ import annotations

import re

_SECTION_HEADERS = {
    "experience",
    "education",
    "projects",
    "skills",
    "technical skills",
    "achievements",
    "certifications",
    "volunteer",
    "volunteering",
    "work experience",
    "professional experience",
    "summary",
    "objective",
    "competitive programming",
}

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{3,5}(?!\w)"
)
_NAME_LINE = re.compile(r"^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z.]+){1,4}$")
_ALL_CAPS_NAME = re.compile(r"^[A-Z][A-Z\s.'-]{2,60}$")


def looks_like_resume(filename: str, text: str) -> bool:
    name = filename.lower()
    if "resume" in name or re.search(r"(^|[^a-z])cv([^a-z]|$)", name):
        return True
    lowered = text.lower()
    hits = sum(1 for header in _SECTION_HEADERS if header in lowered)
    return hits >= 3


def _candidate_name(lines: list[str]) -> str | None:
    """Pick the most likely person name from a resume's lines.

    PDF extractors often dump the header block last. Prefer a short ALL-CAPS or
    Title-Case line that sits next to contact info, and never a section header.
    """
    contact_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if _EMAIL.search(line) or ("linkedin" in line.lower() and "@" not in line.lower())
        ),
        None,
    )
    search_order: list[int] = []
    if contact_idx is not None:
        search_order.extend(range(contact_idx - 1, max(-1, contact_idx - 4), -1))
        search_order.append(contact_idx)
    # Also scan the first and last handful of lines — covers both layouts.
    search_order.extend(range(min(8, len(lines))))
    search_order.extend(range(len(lines) - 1, max(-1, len(lines) - 8), -1))

    seen: set[int] = set()
    for i in search_order:
        if i in seen or i < 0 or i >= len(lines):
            continue
        seen.add(i)
        line = lines[i].strip(" |")
        if not line or len(line) > 60:
            continue
        if line.lower() in _SECTION_HEADERS:
            continue
        if _EMAIL.search(line) or _PHONE.fullmatch(line.replace(" ", "").replace("-", "")):
            continue
        if any(ch.isdigit() for ch in line) and not _ALL_CAPS_NAME.match(line):
            continue
        if _ALL_CAPS_NAME.match(line) or _NAME_LINE.match(line):
            # Title-case the ALL-CAPS form so the graph doesn't shout.
            return " ".join(part.capitalize() for part in line.split())
    return None


def _scrub_contacts(text: str) -> str:
    text = _EMAIL.sub(" (email on file) ", text)
    # Phone: only scrub digit-heavy tokens that look like numbers, not years/ratings.
    def _phone_sub(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 13:
            return " (phone on file) "
        return match.group(0)

    return _PHONE.sub(_phone_sub, text)


_SUBJECT_MARKER = re.compile(
    r"^<!--\s*resume-subject:\s*(.+?)\s*-->\s*", re.IGNORECASE | re.MULTILINE
)


def extract_resume_subject(text: str) -> str | None:
    match = _SUBJECT_MARKER.search(text)
    return match.group(1).strip() if match else None


def strip_resume_markers(text: str) -> str:
    return _SUBJECT_MARKER.sub("", text).lstrip()


def normalize_resume_text(text: str, filename: str = "") -> str:
    """Return extraction-friendly resume text, or the original when not a resume."""
    if not looks_like_resume(filename, text):
        return text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = _candidate_name(lines)
    scrubbed = _scrub_contacts(text)

    brief_parts = [
        "Document type: professional resume / CV.",
        "Extract the candidate as an Employee.",
        "Extract employers, schools and competitions as Organizations.",
        "Extract projects, products and technologies as Products.",
        "Do not extract section headers, ratings, phone numbers or email addresses as entities.",
        "Prefer relations: BELONGS_TO (person→employer/school), USES (person/project→technology), MANAGED_BY (project→person).",
    ]
    if name:
        brief_parts.insert(1, f"Candidate name: {name}.")

    header = " ".join(brief_parts)
    # Marker is consumed by build_episodes so every chunk — not just the first —
    # still knows who the resume is about when the name lived only in the header.
    marker = f"<!-- resume-subject: {name} -->\n" if name else ""
    if name and not scrubbed.lower().startswith(name.lower()):
        return f"{marker}{header}\n\n{name}\n\n{scrubbed}"
    return f"{marker}{header}\n\n{scrubbed}"
