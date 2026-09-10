import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_manifest.py"
TEMPLATE = REPO_ROOT / "release-manifest.template.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Any {{...}} handlebars-style token, or the specific REPLACE_* placeholders
# used in release-manifest.template.json.
PLACEHOLDER_PATTERN = re.compile(r"\{\{.*?\}\}|REPLACE_[A-Z_]+|TODO", re.IGNORECASE)


class GenerateManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.wheel_path = self.tmp_dir / "pcbf_football-0.0.1-py3-none-any.whl"
        self.wheel_bytes = b"fake wheel contents for hashing test"
        self.wheel_path.write_bytes(self.wheel_bytes)
        self.output_path = self.tmp_dir / "release-manifest.json"

    def run_generator(self, commit_sha: str = "a" * 40) -> dict:
        subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--template",
                str(TEMPLATE),
                "--wheel",
                str(self.wheel_path),
                "--commit-sha",
                commit_sha,
                "--pyproject",
                str(PYPROJECT),
                "--output",
                str(self.output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(self.output_path.read_text(encoding="utf-8"))

    def test_manifest_has_no_template_placeholders(self):
        manifest = self.run_generator()
        rendered = json.dumps(manifest)
        self.assertIsNone(
            PLACEHOLDER_PATTERN.search(rendered),
            f"Placeholder token survived generation: {rendered}",
        )

    def test_manifest_matches_actual_wheel_file(self):
        manifest = self.run_generator()
        expected_hash = hashlib.sha256(self.wheel_bytes).hexdigest()

        self.assertEqual(manifest["package_file"], self.wheel_path.name)
        self.assertEqual(manifest["package_sha256"], f"sha256:{expected_hash}")

    def test_required_fields_present_and_typed(self):
        commit_sha = "b" * 40
        manifest = self.run_generator(commit_sha=commit_sha)

        self.assertEqual(manifest["classification_ceiling"], "RESEARCH-MODEL")
        self.assertIs(manifest["cash_admitted"], False)

        self.assertIn("git_commit", manifest)
        self.assertEqual(manifest["git_commit"], commit_sha)
        self.assertIsInstance(manifest["git_commit"], str)
        self.assertTrue(manifest["git_commit"])

        self.assertIn("artifact_version", manifest)
        self.assertIsInstance(manifest["artifact_version"], str)
        self.assertTrue(manifest["artifact_version"])


if __name__ == "__main__":
    unittest.main()
