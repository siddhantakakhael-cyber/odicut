"""Verify PyInstaller frozen-path resolution (web assets + bundled binary)."""
import sys
import tempfile
import unittest
from pathlib import Path


class TestFrozenPaths(unittest.TestCase):
    def setUp(self):
        self._frozen = getattr(sys, "frozen", None)
        self._meipass = getattr(sys, "_MEIPASS", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.meipass = Path(self.tmp.name)
        (self.meipass / "ochub" / "web").mkdir(parents=True)
        (self.meipass / "ochub" / "web" / "index.html").write_text("<html>OpenCode Hub</html>")
        (self.meipass / "vendor").mkdir()
        sys.frozen = True
        sys._MEIPASS = str(self.meipass)

    def tearDown(self):
        if self._frozen is None:
            del sys.frozen
        else:
            sys.frozen = self._frozen
        if self._meipass is None:
            if hasattr(sys, "_MEIPASS"):
                del sys._MEIPASS
        else:
            sys._MEIPASS = self._meipass
        self.tmp.cleanup()

    def test_web_dir_resolves_to_bundle(self):
        from ochub.server import web_dir
        self.assertEqual(web_dir(), self.meipass / "ochub" / "web")
        self.assertTrue((web_dir() / "index.html").exists())

    def test_bundled_binary_found(self):
        from ochub.opencode import _bundled_binary, _exe_name
        binpath = self.meipass / "vendor" / _exe_name()
        binpath.write_bytes(b"fake")
        found = _bundled_binary()
        self.assertEqual(found, binpath)

if __name__ == "__main__":
    unittest.main()
