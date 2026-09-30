import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.check_selection_reuse import verify, write_digest


class SelectionReuseTests(unittest.TestCase):
    def test_exact_bytes_required(self):
        selection = b'{"version":4}\n'
        verify(selection, hashlib.sha256(selection).hexdigest() + "\n")
        with self.assertRaisesRegex(ValueError, "不同"):
            verify(selection + b" ", hashlib.sha256(selection).hexdigest())

    def test_missing_or_invalid_digest_rejected(self):
        for digest in ("", "0" * 63, "G" * 64):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "有效"):
                verify(b"{}", digest)

    def test_release_digest_file_round_trip(self):
        selection = b'{"version":4}\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.sha256"
            write_digest(selection, path)
            self.assertEqual(path.read_text(encoding="ascii"), hashlib.sha256(selection).hexdigest() + "\n")
            verify(selection, path.read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
