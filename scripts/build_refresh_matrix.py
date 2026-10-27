#!/usr/bin/env python3
"""Build the workflow_call matrix for the scheduled refresh, from
pack-sources.json (relation IDs) joined with packs.json (display names).
Writes GITHUB_OUTPUT: matrix (a {"include": [...]} JSON object), has_packs.
"""
import json
import os
import sys


def main() -> int:
    with open("pack-sources.json", encoding="utf-8") as f:
        sources = json.load(f)
    with open("packs.json", encoding="utf-8") as f:
        packs = {p["slug"]: p for p in json.load(f)["packs"]}

    include = [
        {"slug": slug, "name": packs[slug]["name"], "relation_id": src["relationId"]}
        for slug, src in sources.items()
        if slug in packs
    ]
    matrix = json.dumps({"include": include}, ensure_ascii=False)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"matrix={matrix}\n")
            f.write(f"has_packs={'true' if include else 'false'}\n")
    print(f"Refresh matrix: {matrix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
