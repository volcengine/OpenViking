"""A token-hashing exception must never hide unrelated security findings."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/codeql_token_digest.py"
SPEC = importlib.util.spec_from_file_location("codeql_token_digest", SCRIPT)
exception = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exception)


class TokenDigestExceptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for path in (exception.SOURCE, exception.ISSUER):
            destination = self.root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
        self.span = exception.reviewed_region(self.root)
        self.assertIsNotNone(self.span)

    def finding(self, rule=exception.RULE, uri=exception.SOURCE):
        return {
            "ruleId": rule,
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {
                            "startLine": self.span[0],
                            "startColumn": self.span[1],
                            "endLine": self.span[2],
                            "endColumn": self.span[3],
                        },
                    }
                }
            ],
            "message": {"text": "fixture"},
            "partialFingerprints": {"primaryLocationLineHash": "fixture:1"},
        }

    def document(self, findings):
        return {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "CodeQL", "rules": [{"id": exception.RULE}]}},
                    "automationDetails": {"id": "/language:python/"},
                    "results": findings,
                }
            ],
        }

    def rewrite(self, path, before, after):
        destination = self.root / path
        destination.write_text(
            destination.read_text(encoding="utf-8").replace(before, after), encoding="utf-8"
        )

    def test_removes_only_the_reviewed_result_and_preserves_metadata(self):
        other_rule = self.finding("py/clear-text-storage-sensitive-data")
        other_file = self.finding(uri="other.py")
        other_line = self.finding()
        other_line["locations"][0]["physicalLocation"]["region"]["startLine"] += 1
        findings = [self.finding(), other_rule, other_file, other_line]
        document = self.document(findings)
        expected = copy.deepcopy(document)
        expected["runs"][0]["results"] = [other_rule, other_file, other_line]
        self.assertEqual(exception.filter_sarif(document, self.span), 1)
        self.assertEqual(document, expected)

    def test_tracks_line_moves(self):
        self.rewrite(exception.SOURCE, '"""Shared relational', '\n\n"""Shared relational')
        moved = exception.reviewed_region(self.root)
        self.assertEqual(moved, (self.span[0] + 2, self.span[1], self.span[2] + 2, self.span[3]))
        self.assertFalse(exception.is_exception(self.finding(), moved))

    def test_missing_marker_changed_hash_or_added_statement_retains_findings(self):
        original = (self.root / exception.SOURCE).read_text(encoding="utf-8")
        for before, after in (
            (exception.MARKER, "# Removed exception"),
            ("hashlib.sha256(api_key.encode())", "hashlib.md5(api_key.encode())"),
            (
                "return hashlib.sha256(api_key.encode())",
                "api_key = 'weak'\n    return hashlib.sha256(api_key.encode())",
            ),
        ):
            with self.subTest(after=after):
                (self.root / exception.SOURCE).write_text(original, encoding="utf-8")
                self.rewrite(exception.SOURCE, before, after)
                span = exception.reviewed_region(self.root)
                self.assertIsNone(span)
                document = self.document([self.finding()])
                expected = copy.deepcopy(document)
                self.assertEqual(exception.filter_sarif(document, span), 0)
                self.assertEqual(document, expected)

    def test_weaker_default_issuance_disables_exception(self):
        self.rewrite(exception.ISSUER, "secrets.token_hex(32)", "secrets.token_hex(8)")
        self.assertIsNone(exception.reviewed_region(self.root))

    def test_missing_or_ambiguous_locations_are_preserved(self):
        candidates = [{}, {"ruleId": exception.RULE, "locations": []}]
        for key in ("artifactLocation", "region"):
            candidate = self.finding()
            candidate["locations"][0]["physicalLocation"][key] = None
            candidates.append(candidate)
        candidate = self.finding()
        candidate["locations"].append(self.finding(uri="other.py")["locations"][0])
        candidates.append(candidate)
        candidate = self.finding()
        candidate["locations"][0]["physicalLocation"]["region"]["startColumn"] = 1
        candidates.append(candidate)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertFalse(exception.is_exception(candidate, self.span))

    def test_other_tools_and_empty_runs_are_preserved(self):
        document = self.document([self.finding()])
        document["runs"][0]["tool"]["driver"]["name"] = "Other scanner"
        document["runs"].extend([{}, {"tool": {"driver": {"name": "CodeQL"}}, "results": None}])
        expected = copy.deepcopy(document)
        self.assertEqual(exception.filter_sarif(document, self.span), 0)
        self.assertEqual(document, expected)

    def test_cli_preserves_original_sarif(self):
        source = self.root / "original"
        output = self.root / "upload"
        source.mkdir()
        path = source / "python.sarif"
        original = json.dumps(self.document([self.finding()]))
        path.write_text(original, encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-root",
                str(self.root),
                "--input",
                str(source),
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(json.loads((output / path.name).read_text())["runs"][0]["results"], [])


if __name__ == "__main__":
    unittest.main()
