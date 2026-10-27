#!/usr/bin/env python3
"""Validate SLUG/RELATION_ID before they touch any shell command, URL, or file path.

Reads SLUG and RELATION_ID from the environment (as bound by the calling
workflow step) and exits non-zero with a message suitable for posting back
to the originating issue if either is malformed.
"""
import os
import re
import sys

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RELATION_ID_RE = re.compile(r"^[0-9]+$")
MAX_SLUG_LEN = 32


def validate_slug(slug: str) -> str | None:
    if not slug:
        return "Slug is required."
    if len(slug) > MAX_SLUG_LEN:
        return f"Slug must be at most {MAX_SLUG_LEN} characters."
    if not SLUG_RE.match(slug):
        return "Slug must be lowercase alphanumeric words separated by single hyphens (e.g. `ffb`, `lk-muenchen`)."
    return None


def validate_relation_id(relation_id: str) -> str | None:
    if not relation_id:
        return "OSM relation ID is required."
    if not RELATION_ID_RE.match(relation_id):
        return "OSM relation ID must be a plain positive integer (the number after `relation/` on openstreetmap.org, no `R` prefix)."
    return None


def main() -> int:
    slug = os.environ.get("SLUG", "")
    relation_id = os.environ.get("RELATION_ID", "")

    errors = [
        msg
        for msg in (validate_slug(slug), validate_relation_id(relation_id))
        if msg
    ]

    if errors:
        for msg in errors:
            print(f"::error::{msg}", file=sys.stderr)
        print("\n".join(errors))
        return 1

    print(f"Inputs OK: slug={slug!r} relation_id={relation_id!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
