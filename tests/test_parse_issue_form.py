import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "parse_issue_form.py")
FIELDS = '[["Slug","slug"]]'


def run(body):
    with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as out:
        env = {**os.environ, "ISSUE_BODY": body, "FIELDS_JSON": FIELDS, "GITHUB_OUTPUT": out.name}
        proc = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True)
        return proc.returncode, open(out.name, encoding="utf-8").read()


class ParseIssueForm(unittest.TestCase):
    def test_a_single_line_field_reaches_the_output(self):
        self.assertEqual(run("### Slug\n\nffb\n"), (0, "slug=ffb\n"))

    def test_a_field_spanning_lines_cannot_inject_an_output_key(self):
        code, output = run("### Slug\n\nffb\nrelation_id=1\n")
        self.assertEqual((code, output), (1, ""))


if __name__ == "__main__":
    unittest.main()
