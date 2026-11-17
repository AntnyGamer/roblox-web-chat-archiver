"""Exercise the actual workflow packaging code without publishing or using credentials."""
import contextlib
import io
import os
from pathlib import Path
import re
import shutil
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from zipfile import ZipFile


class ReleaseTests(unittest.TestCase):
    def test_r3_package_contains_exact_distribution_bytes(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
        match = re.search(r"          python - <<'PY'\n(.*?)\n          PY", workflow, re.S)
        self.assertIsNotNone(match, "The release packaging script was not found")
        script = textwrap.dedent(match.group(1))
        files = ("app.py", "archiver.ps1", "LICENSE", "README.md", "run_archiver.bat", "viewer.html")
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in files:
                shutil.copyfile(root / name, directory / name)
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {"RELEASE_TAG": "v2.0.0-r3", "GITHUB_OUTPUT": str(directory / "output.txt")}), \
                     contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(script, "release.yml", "exec"), {"__name__": "__main__"})
                archive = directory / "roblox-web-chat-archiver-v2.0.0-r3.zip"
                with ZipFile(archive) as zipped:
                    self.assertEqual(zipped.namelist(), list(files))
                    self.assertIsNone(zipped.testzip())
                    for name in files:
                        self.assertEqual(zipped.read(name), (root / name).read_bytes())
                self.assertEqual((directory / "output.txt").read_text(encoding="utf-8"),
                                 "path=roblox-web-chat-archiver-v2.0.0-r3.zip\n")
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
