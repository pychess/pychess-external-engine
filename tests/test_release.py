from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.release_check import check_distributions, check_versions


class ReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.dist / ".gitignore").write_bytes(b"*")
        self.stem = "pychess_external_engine-0.1.0"
        self.wheel = self.dist / f"{self.stem}-py3-none-any.whl"
        self.source = self.dist / f"{self.stem}.tar.gz"
        self.info = self.stem + ".dist-info"
        files = {
            "pyproject.toml": '[project]\nname="pychess-external-engine"\nversion="0.1.0"\n'
            'requires-python=">=3.11"\nlicense="AGPL-3.0-or-later"\n'
            'dependencies=["pyffish==0.0.90", "pyffish-alice==0.0.3"]\n',
            "uv.lock": '[[package]]\nname="pychess-external-engine"\nversion="0.1.0"\n',
            "src/pychess_external_engine/__init__.py": '__version__ = "0.1.0"\n',
            "README.md": "# Engine\nAccount → External engines\n",
            "LICENSE": "AGPL attribution\n",
            "NOTICE.md": "Upstream attribution\n",
            "AGENTS.md": "Release instructions\n",
            "MANIFEST.in": "include LICENSE NOTICE.md\n",
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.metadata = (
            "Metadata-Version: 2.4\nName: pychess-external-engine\nVersion: 0.1.0\n"
            "Requires-Python: >=3.11\nLicense-Expression: AGPL-3.0-or-later\n"
            "Description-Content-Type: text/markdown\n"
            "Requires-Dist: pyffish==0.0.90\nRequires-Dist: pyffish-alice==0.0.3\n\n"
            + files["README.md"]
        ).encode()
        self.wheel_files = {
            "pychess_external_engine/__init__.py": files[
                "src/pychess_external_engine/__init__.py"
            ].encode(),
            f"{self.info}/METADATA": self.metadata,
            f"{self.info}/entry_points.txt": (
                b"[console_scripts]\npychess-external-engine = pychess_external_engine.cli:main\n"
            ),
            f"{self.info}/licenses/LICENSE": files["LICENSE"].encode(),
            f"{self.info}/licenses/NOTICE.md": files["NOTICE.md"].encode(),
        }
        for name in ("WHEEL", "RECORD", "top_level.txt"):
            self.wheel_files[f"{self.info}/{name}"] = b""
        self.source_files = {
            f"{self.stem}/{name}": content.encode() for name, content in files.items()
        }
        self.source_files[f"{self.stem}/PKG-INFO"] = self.metadata
        self.source_files[f"{self.stem}/setup.cfg"] = b""
        for name in (
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "entry_points.txt",
            "requires.txt",
            "top_level.txt",
        ):
            self.source_files[f"{self.stem}/src/pychess_external_engine.egg-info/{name}"] = b""
        self.write_archives()

    def write_archives(self) -> None:
        with zipfile.ZipFile(self.wheel, "w") as wheel:
            for name, content in self.wheel_files.items():
                wheel.writestr(name, content)
        with tarfile.open(self.source, "w:gz") as source:
            for name, content in self.source_files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                source.addfile(member, io.BytesIO(content))

    def test_versions_and_matching_tag(self) -> None:
        self.assertEqual(check_versions(self.root), "0.1.0")
        self.assertEqual(check_versions(self.root, "v0.1.0"), "0.1.0")
        for tag in ("0.1.0", "v0.1.1", "v0.1.0a1"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "Release tag"):
                check_versions(self.root, tag)

    def test_inconsistent_runtime_and_lock_versions(self) -> None:
        for name in ("src/pychess_external_engine/__init__.py", "uv.lock"):
            path = self.root / name
            original = path.read_text()
            path.write_text(original.replace("0.1.0", "0.1.1"))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "version differs"):
                check_versions(self.root)
            path.write_text(original)

    def test_valid_archives_write_and_recheck_hashes(self) -> None:
        check_distributions(self.root, self.dist, "0.1.0")
        manifest = (self.dist / "SHA256SUMS").read_text()
        self.assertIn(self.wheel.name, manifest)
        self.assertIn(self.source.name, manifest)
        check_distributions(self.root, self.dist, "0.1.0")
        self.assertEqual((self.dist / "SHA256SUMS").read_text(), manifest)

    def test_stale_distribution_is_rejected(self) -> None:
        (self.dist / "old-release.whl").write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "only this version"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_wheel_runtime_change_is_rejected(self) -> None:
        self.wheel_files["pychess_external_engine/__init__.py"] = b"stale source"
        self.write_archives()
        with self.assertRaisesRegex(ValueError, "Wheel source differs"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_missing_required_alice_dependency_is_rejected(self) -> None:
        self.wheel_files[f"{self.info}/METADATA"] = self.metadata.replace(
            b"Requires-Dist: pyffish-alice==0.0.3\n", b""
        )
        self.write_archives()
        with self.assertRaisesRegex(ValueError, "Required rules dependency missing: pyffish-alice"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_unexpected_bundled_file_is_rejected(self) -> None:
        self.source_files[f"{self.stem}/.pypirc"] = b"fixture credential"
        self.write_archives()
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_source_attribution_change_is_rejected(self) -> None:
        self.source_files[f"{self.stem}/NOTICE.md"] = b"missing upstream attribution"
        self.write_archives()
        with self.assertRaisesRegex(ValueError, "Source archive differs: NOTICE.md"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_unsafe_archive_path_is_rejected(self) -> None:
        self.source_files[f"{self.stem}/../outside"] = b"unsafe"
        self.write_archives()
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            check_distributions(self.root, self.dist, "0.1.0")

    def test_hash_change_after_validation_is_rejected(self) -> None:
        check_distributions(self.root, self.dist, "0.1.0")
        with self.wheel.open("ab") as wheel:
            wheel.write(b"changed archive")
        with self.assertRaisesRegex(ValueError, "hashes changed"):
            check_distributions(self.root, self.dist, "0.1.0")


if __name__ == "__main__":
    unittest.main()
