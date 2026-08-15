"""End-to-end: sign a tree, verify it, tamper with it, catch every case."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pk.manifest import create_manifest, verify_manifest
from pk.merkle import hash_tree


def make_tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hello')\n")
    (root / "src" / "util.py").write_text("def f():\n    return 42\n")
    (root / "README.md").write_text("# demo\n")


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        make_tree(self.root)
        self.key = Ed25519PrivateKey.generate()
        self.manifest = create_manifest(self.root, self.key, author="test")

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_verify_passes(self):
        self.assertEqual(verify_manifest(self.manifest, self.root), [])

    def test_tree_hash_deterministic(self):
        h1, _ = hash_tree(self.root)
        h2, _ = hash_tree(self.root)
        self.assertEqual(h1, h2)

    def test_modified_file_detected(self):
        (self.root / "src" / "main.py").write_text("print('evil')\n")
        problems = verify_manifest(self.manifest, self.root)
        self.assertTrue(any("modified since signing: src/main.py" in p for p in problems))

    def test_added_file_detected(self):
        (self.root / "backdoor.py").write_text("x = 1\n")
        problems = verify_manifest(self.manifest, self.root)
        self.assertTrue(any("added since signing: backdoor.py" in p for p in problems))

    def test_deleted_file_detected(self):
        (self.root / "README.md").unlink()
        problems = verify_manifest(self.manifest, self.root)
        self.assertTrue(any("missing since signing: README.md" in p for p in problems))

    def test_rename_detected(self):
        (self.root / "README.md").rename(self.root / "README2.md")
        problems = verify_manifest(self.manifest, self.root)
        self.assertTrue(any("missing since signing: README.md" in p for p in problems))
        self.assertTrue(any("added since signing: README2.md" in p for p in problems))

    def test_payload_tamper_breaks_signature(self):
        forged = json.loads(json.dumps(self.manifest))
        forged["payload"]["author"] = "someone else"
        problems = verify_manifest(forged, self.root)
        self.assertTrue(any("signature is INVALID" in p for p in problems))

    def test_resign_with_other_key_fails_pubkey_pin(self):
        # Strip-and-resign: attacker signs the same content with their key.
        attacker = Ed25519PrivateKey.generate()
        forged = create_manifest(self.root, attacker, author="attacker")
        from pk.keys import public_key_hex

        honest_hex = public_key_hex(self.key.public_key())
        problems = verify_manifest(forged, self.root, expected_public_key_hex=honest_hex)
        self.assertTrue(any("does not match the expected key" in p for p in problems))
        # But without the pin, the forgery verifies — this is exactly why
        # anchoring (priority) exists.
        self.assertEqual(verify_manifest(forged, self.root), [])

    def test_excluded_dirs_ignored(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("noise\n")
        self.assertEqual(verify_manifest(self.manifest, self.root), [])


if __name__ == "__main__":
    unittest.main()
