"""Parsing and normalization layer.

Source bytes in, plain normalized text out. No ML, no network. Unchanged in
spirit from v1 — Graphiti consumes normalized episodes, never raw sources.
"""

from kg_parsing.parsers import SUPPORTED_EXTENSIONS, ParseError, parse_document

__all__ = ["ParseError", "SUPPORTED_EXTENSIONS", "parse_document"]
