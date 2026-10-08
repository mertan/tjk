"""Real Unix permission regressions for Android-style searchable ancestors."""

import errno
import fcntl
import os
from pathlib import Path
import tempfile
import unittest

from termux_manager.storage import (
    MAX_JSON_BYTES, Store, StoreError, _open_directory_path, read_private_json,
    read_private_key,
)


@unittest.skipUnless(hasattr(os, "O_PATH"), "search-only traversal requires O_PATH")
class DirectoryTraversalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.ancestor = self.base / "search-only"
        self.project = self.ancestor / "private-project"
        self.project.mkdir(mode=0o700, parents=True)
        self.runtime = self.project / "runtime"
        self.runtime.mkdir(mode=0o700)
        for name in ("inputs", "jobs", "results", "nonces"):
            (self.runtime / name).mkdir(mode=0o700)
        self.key = self.project / "auth.key"
        self.key.write_text("a" * 64)
        self.key.chmod(0o600)
        self.ancestor.chmod(0o111)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.ancestor.chmod, 0o700)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses Unix read permission checks")
    def test_search_only_ancestor_allows_private_key_and_store(self):
        # This permission failure proves the original O_RDONLY walk would fail;
        # direct access to known children nevertheless works for this same UID.
        with self.assertRaises(PermissionError) as denied:
            os.open(self.ancestor, os.O_RDONLY | os.O_DIRECTORY)
        self.assertEqual(denied.exception.errno, errno.EACCES)
        self.assertEqual(len(self.key.read_bytes()), 64)
        self.assertEqual(read_private_key(self.key), bytes.fromhex("a" * 64))
        with Store(self.runtime) as store:
            store.create("inputs", "b" * 32, {"private": True})
            self.assertEqual(store.read("inputs", "b" * 32), {"private": True})
            self.assertEqual(store.counts()["inputs"], 1)
            self.assertTrue(store.reserve_nonce("c" * 32, 1000))
        self.assertEqual(self.ancestor.stat().st_mode & 0o777, 0o111)

    def test_final_descriptor_supports_listing_locking_and_sync(self):
        fd = _open_directory_path(self.runtime)
        try:
            self.assertFalse(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_PATH)
            self.assertEqual(set(os.listdir(fd)), {"inputs", "jobs", "results", "nonces"})
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(fd)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def test_symlink_ancestor_beneath_search_only_directory_is_rejected(self):
        # Temporarily allow creation; traversal itself occurs with mode 0111.
        self.ancestor.chmod(0o700)
        alias = self.ancestor / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        self.ancestor.chmod(0o111)
        for operation in (lambda: Store(alias / "runtime"),
                          lambda: read_private_key(alias / "auth.key")):
            with self.subTest(operation=operation), self.assertRaises(StoreError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "unsafe_directory")
            self.assertEqual(caught.exception.__cause__.errno, errno.ENOTDIR)

    def test_final_symlink_is_rejected_with_search_only_ancestor(self):
        alias = self.project / "alias"
        alias.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises(StoreError) as caught:
            Store(alias)
        self.assertEqual(caught.exception.code, "unsafe_directory")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses Unix search permission checks")
    def test_missing_search_permission_still_fails_closed(self):
        self.ancestor.chmod(0o400)
        with self.assertRaises(StoreError) as caught:
            Store(self.runtime)
        self.assertEqual(caught.exception.code, "unsafe_directory")
        self.assertEqual(caught.exception.__cause__.errno, errno.EACCES)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses Unix read permission checks")
    def test_unreadable_final_directory_is_not_bypassed(self):
        self.runtime.chmod(0o100)
        self.addCleanup(self.runtime.chmod, 0o700)
        with self.assertRaises(StoreError) as caught:
            Store(self.runtime)
        self.assertEqual(caught.exception.code, "unsafe_directory")
        self.assertEqual(caught.exception.__cause__.errno, errno.EACCES)

    def test_final_private_mode_is_still_enforced(self):
        self.runtime.chmod(0o755)
        with self.assertRaises(StoreError) as caught:
            Store(self.runtime)
        self.assertEqual(caught.exception.code, "unsafe_directory")


class PrivateJsonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "project.json"
        self.path.write_text('{"project_id":"' + "a" * 32 + '"}')
        self.path.chmod(0o600)
        self.addCleanup(self.temporary.cleanup)

    def test_private_json_reads_without_mutation(self):
        before = self.path.read_bytes()
        self.assertEqual(read_private_json(self.path), {"project_id": "a" * 32})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_json_strict_validation(self):
        for raw in (b'{"a":1,"a":2}', b'[]', b'{"a":NaN}', b'{invalid'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(StoreError) as caught:
                    read_private_json(self.path)
                self.assertEqual(caught.exception.code, "invalid_json")

    def test_size_cap(self):
        self.path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
        with self.assertRaises(StoreError) as caught:
            read_private_json(self.path)
        self.assertEqual(caught.exception.code, "unsafe_file")

    def test_symlink_and_hardlink_are_rejected(self):
        alias = self.base / "alias.json"
        alias.symlink_to(self.path)
        with self.assertRaises(StoreError) as caught:
            read_private_json(alias)
        self.assertEqual(caught.exception.code, "unsafe_file")
        alias.unlink()
        os.link(self.path, alias)
        with self.assertRaises(StoreError) as caught:
            read_private_json(self.path)
        self.assertEqual(caught.exception.code, "unsafe_file")

    def test_nonprivate_mode_is_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaises(StoreError) as caught:
            read_private_json(self.path)
        self.assertEqual(caught.exception.code, "unsafe_file")


if __name__ == "__main__":
    unittest.main()
