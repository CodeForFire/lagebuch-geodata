#!/usr/bin/env python3
"""Extract field values from a rendered GitHub Issue Forms body.

Issue Forms render each field as a "### <label>" heading followed by the
submitted value (or the literal "_No response_" for an empty optional
field). This just walks those headings -- it does not interpret or
validate the values (validate_inputs.py / lookup_boundary.py do that).

Env vars: ISSUE_BODY (the issue body text), FIELDS_JSON (a JSON array of
[label, output_key] pairs matching the issue template's field labels, in
the order they appear in the template).

Writes each output_key=value to GITHUB_OUTPUT (value is "" for
"_No response_" or a missing heading).
"""
import json
import os
import re
import sys


def main() -> int:
    body = os.environ.get("ISSUE_BODY", "")
    fields = json.loads(os.environ["FIELDS_JSON"])

    values = {}
    for label, key in fields:
        pattern = re.compile(
            rf"^###\s+{re.escape(label)}\s*$\n+(.*?)(?=^###\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(body)
        value = match.group(1).strip() if match else ""
        if value == "_No response_":
            value = ""
        values[key] = value

    github_output = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in values.items()]
    print("\n".join(lines))
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            for key, value in values.items():
                if "\n" in value:
                    delimiter = "EOF_PARSE_ISSUE_FORM"
                    f.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
                else:
                    f.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
