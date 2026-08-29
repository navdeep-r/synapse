from kg_parsing.resume import looks_like_resume, normalize_resume_text


def test_detects_resume_by_filename() -> None:
    assert looks_like_resume("SaajidAhamed-Resume.pdf", "hello world")


def test_detects_resume_by_sections() -> None:
    text = "EXPERIENCE\nfoo\nEDUCATION\nbar\nPROJECTS\nbaz\nTECHNICAL SKILLS\nqux"
    assert looks_like_resume("notes.txt", text)


def test_normalize_promotes_name_and_scrubs_contacts() -> None:
    raw = """\
COMPETITIVE PROGRAMMING
Leetcode: 1912
EXPERIENCE
VK Tutelage Pvt Ltd - Backend Engineer Intern
EDUCATION
Chennai Institute of Technology
PROJECTS
SmartIn
TECHNICAL SKILLS
Python, Redis
SAAJID AHAMED A
6385512445 | saajidahamed01@gmail.com | LinkedIn | GitHub
"""
    out = normalize_resume_text(raw, "SaajidAhamed-Resume.pdf")
    assert "<!-- resume-subject: Saajid Ahamed A -->" in out
    assert "Candidate name: Saajid Ahamed A" in out
    assert out.index("Saajid Ahamed A") < out.index("EXPERIENCE")
    assert "saajidahamed01@gmail.com" not in out
    assert "6385512445" not in out
    assert "email on file" in out
    assert "phone on file" in out


def test_subject_is_injected_into_every_episode() -> None:
    from datetime import UTC, datetime

    from kg_contracts import Chunk, Provenance
    from kg_prefilter.batching import build_episodes

    now = datetime.now(UTC)

    def chunk(cid: str, text: str, ordinal: int) -> Chunk:
        return Chunk(
            chunk_id=cid,
            document_id="doc-1",
            ordinal=ordinal,
            text=text,
            offset=ordinal * 100,
            char_length=len(text),
            content_hash=str(ordinal),
            provenance=Provenance(
                chunk_id=cid,
                document_id="doc-1",
                source="upload",
                source_type="upload",
                source_document_name="resume.pdf",
                offset=ordinal * 100,
                timestamp=now,
            ),
        )

    chunks = [
        chunk(
            "c0",
            "<!-- resume-subject: Saajid Ahamed A -->\nDocument type: resume.\n\nSaajid Ahamed A",
            0,
        ),
        chunk("c1", "VK Tutelage Pvt Ltd - Backend Engineer Intern", 1),
    ]
    episodes = build_episodes(chunks, target_chars=50)
    assert len(episodes) >= 2
    later = next(ep for ep in episodes if "VK Tutelage" in ep.body)
    assert "Saajid Ahamed A" in later.body
    assert "resume of Saajid Ahamed A" in later.source_description
    assert "<!-- resume-subject" not in later.body


def test_non_resume_passes_through() -> None:
    text = "Alice reports to Robert at Acme."
    assert normalize_resume_text(text, "handbook.txt") == text
