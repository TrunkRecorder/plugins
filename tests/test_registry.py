"""Tests for tools/registry.py that need no network: python3 -m unittest"""
import copy
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import registry  # noqa: E402

SHA = "a" * 64


def entry(**over):
    e = {
        "id": "pager",
        "name": "Pager",
        "description": "Pages your phone.",
        "repository": "https://github.com/someone/trunk-plugin-pager",
        "license": "MIT",
        "tier": "community",
        "version": "1.2.0",
        "api": 1,
        "tag": "v1.2.0",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "assets": {},
    }
    for t in registry.TARGETS:
        e["assets"][t] = {"url": registry.asset_url(e, t), "sha256": SHA}
    e.update(over)
    return e


class Validate(unittest.TestCase):
    def test_a_good_entry(self):
        self.assertEqual(registry.validate(entry(), "pager"), [])
        self.assertEqual(registry.validate(entry(homepage="https://example.com"), "pager"), [])

    def test_the_file_name_is_the_id(self):
        self.assertIn("id: 'pager', but the file is plugins/other.json", registry.validate(entry(), "other"))

    def test_official_only_in_the_organization(self):
        self.assertTrue(any("only plugins in" in p for p in registry.validate(entry(tier="official"))))
        e = entry(repository="https://github.com/TrunkRecorder/trunk-plugin-pager", tier="official")
        e["assets"] = {t: {"url": registry.asset_url(e, t), "sha256": SHA} for t in registry.TARGETS}
        self.assertEqual(registry.validate(e), [])

    def test_urls_must_be_the_releases_own(self):
        e = entry()
        e["assets"]["universal-apple-darwin"]["url"] = "https://evil.example/pager.tar.gz"
        self.assertTrue(any(p.startswith("assets.universal-apple-darwin.url") for p in registry.validate(e)))
        # Also when something else is wrong too.
        e["tier"] = "official"
        self.assertTrue(any(p.startswith("assets.universal-apple-darwin.url") for p in registry.validate(e)))

    def test_linux_x86_64_is_required(self):
        e = entry()
        del e["assets"]["x86_64-unknown-linux-gnu"]
        self.assertIn("assets: needs x86_64-unknown-linux-gnu", registry.validate(e))

    def test_tag_version_api_commit(self):
        self.assertTrue(registry.validate(entry(tag="1.2.0")))
        self.assertTrue(registry.validate(entry(api=99)))
        self.assertTrue(registry.validate(entry(commit="abc123")))
        self.assertTrue(registry.validate(entry(version="1.2")))

    def test_unknown_and_missing_fields(self):
        e = entry(extra=1)
        del e["license"]
        p = registry.validate(e)
        self.assertIn("extra: not a field the registry knows", p)
        self.assertIn("license: missing", p)


class Manifest(unittest.TestCase):
    def manifest(self, **over):
        m = {"id": "pager", "name": "Pager", "description": "Pages your phone.", "version": "1.2.0", "api": 1,
             "repository": "https://github.com/someone/trunk-plugin-pager.git", "subscribe": ["call.start"]}
        m.update(over)
        return m

    def test_matches(self):
        self.assertEqual(registry.check_manifest(self.manifest(), entry()), [])

    def test_differs(self):
        self.assertTrue(registry.check_manifest(self.manifest(version="1.1.0"), entry()))
        self.assertTrue(registry.check_manifest(self.manifest(repository="https://github.com/other/x"), entry()))
        self.assertTrue(registry.check_manifest(self.manifest(subscribe=[]), entry()))
        self.assertTrue(registry.check_manifest(self.manifest(subscribe=["calls"]), entry()))


class Sums(unittest.TestCase):
    def test_parse(self):
        text = f"{SHA}  pager-1.2.0-universal-apple-darwin.tar.gz\n{'B' * 64} *SHA-binary-mode\n\n"
        self.assertEqual(registry.parse_sums(text), {"pager-1.2.0-universal-apple-darwin.tar.gz": SHA, "SHA-binary-mode": "b" * 64})


class Unpack(unittest.TestCase):
    def archive(self, dir, name, link=False):
        path = Path(dir) / "a.tar.gz"
        with tarfile.open(path, "w:gz") as t:
            info = tarfile.TarInfo(name)
            if link:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                t.addfile(info)
            else:
                info.size = 2
                t.addfile(info, io.BytesIO(b"hi"))
        return path

    def test_refuses_escaping_paths_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            for name, link in [("../x", False), ("/abs", False), ("pager/link", True)]:
                with self.assertRaises(registry.Problem):
                    registry.unpack(self.archive(d, name, link), Path(d) / "out")

    def test_unpacks(self):
        with tempfile.TemporaryDirectory() as d:
            registry.unpack(self.archive(d, "pager-1.2.0/pager"), Path(d) / "out")
            self.assertEqual((Path(d) / "out/pager-1.2.0/pager").read_bytes(), b"hi")


class Index(unittest.TestCase):
    def test_the_index_is_up_to_date(self):
        self.assertEqual(registry.INDEX.read_text(), registry.build_index(write=False))

    def test_every_entry_is_valid(self):
        for p in sorted(registry.PLUGINS.glob("*.json")):
            with self.subTest(p.name):
                self.assertEqual(registry.validate(json.loads(p.read_text()), p.stem), [])


if __name__ == "__main__":
    unittest.main()
