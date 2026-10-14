"""Validate release versions and the exact wheel/source files before CI publication."""

from __future__ import annotations

import argparse
import ast
import hashlib
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_versions(root: Path, tag: str = "") -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    module = ast.parse((root / "src/pychess_external_engine/__init__.py").read_text())
    versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    require(versions == [version], "Runtime version differs from pyproject.toml")
    lock = tomllib.loads((root / "uv.lock").read_text())
    locked = [item["version"] for item in lock["package"] if item["name"] == project["name"]]
    require(locked == [version], "Lockfile version differs from pyproject.toml")
    require(not tag or tag == "v" + version, "Release tag must equal v<package-version>")
    return version


def check_names(names: list[str]) -> None:
    require(len(names) == len(set(names)), "Archive contains duplicate paths")
    for name in names:
        path = PurePosixPath(name)
        require(
            not path.is_absolute() and ".." not in path.parts and "\\" not in name,
            "Archive contains an unsafe path",
        )


def check_metadata(data: bytes, root: Path, version: str) -> None:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    metadata = BytesParser(policy=default).parsebytes(data)
    for field, expected in (
        ("Name", project["name"]),
        ("Version", version),
        ("Requires-Python", project["requires-python"]),
        ("License-Expression", project["license"]),
        ("Description-Content-Type", "text/markdown"),
    ):
        require(metadata[field] == expected, f"Incorrect distribution metadata: {field}")
    dependencies = metadata.get_all("Requires-Dist", [])
    for name in ("pyffish", "pyffish-alice"):
        expected = next(item for item in project["dependencies"] if item.startswith(name + "=="))
        require(expected in dependencies, f"Required rules dependency missing: {name}")
    body = metadata.get_payload(decode=True)
    require(isinstance(body, bytes), "Distribution README is missing")
    assert isinstance(body, bytes)
    require(
        body.decode("utf-8").strip() == (root / "README.md").read_text().strip(),
        "Distribution README differs from the checkout",
    )


def check_distributions(root: Path, directory: Path, version: str) -> None:
    stem = f"pychess_external_engine-{version}"
    wheel_name, source_name = f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"
    expected_files = {wheel_name, source_name}
    ignore = directory / ".gitignore"
    if ignore.exists():
        require(ignore.read_bytes() == b"*", "Unexpected build-directory ignore file")
    require(
        {path.name for path in directory.iterdir()} - {"SHA256SUMS", ".gitignore"}
        == expected_files,
        "Distribution directory must contain only this version's wheel and source archive",
    )
    with zipfile.ZipFile(directory / wheel_name) as wheel:
        check_names(wheel.namelist())
        info = f"{stem}.dist-info"
        expected = {
            f"{info}/{name}"
            for name in (
                "METADATA",
                "WHEEL",
                "RECORD",
                "entry_points.txt",
                "top_level.txt",
                "licenses/LICENSE",
                "licenses/NOTICE.md",
            )
        }
        for path in (root / "src/pychess_external_engine").glob("*.py"):
            member = f"pychess_external_engine/{path.name}"
            expected.add(member)
            require(wheel.read(member) == path.read_bytes(), f"Wheel source differs: {path.name}")
        require(set(wheel.namelist()) == expected, "Wheel contains unexpected files")
        for name in ("LICENSE", "NOTICE.md"):
            require(
                wheel.read(f"{info}/licenses/{name}") == (root / name).read_bytes(),
                f"Wheel attribution differs: {name}",
            )
        require(
            wheel.read(f"{info}/entry_points.txt").decode().strip()
            == "[console_scripts]\npychess-external-engine = pychess_external_engine.cli:main",
            "Installed CLI entry point differs",
        )
        check_metadata(wheel.read(f"{info}/METADATA"), root, version)
    with tarfile.open(directory / source_name) as source:
        check_names(source.getnames())
        require(
            all(member.isdir() or member.isfile() for member in source.getmembers()),
            "Source archive contains links or special files",
        )
        required = [
            "AGENTS.md",
            "LICENSE",
            "NOTICE.md",
            "README.md",
            "MANIFEST.in",
            "pyproject.toml",
            "uv.lock",
        ]
        for name in ("docs", "src/pychess_external_engine", "tests", "tools"):
            required.extend(
                str(path.relative_to(root))
                for path in (root / name).glob("*")
                if path.suffix in (".md", ".py")
            )
        expected = {f"{stem}/{name}" for name in required}
        expected.add(f"{stem}/PKG-INFO")
        expected.add(f"{stem}/setup.cfg")
        for name in (
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "entry_points.txt",
            "requires.txt",
            "top_level.txt",
        ):
            expected.add(f"{stem}/src/pychess_external_engine.egg-info/{name}")
        actual = {member.name for member in source.getmembers() if member.isfile()}
        require(actual == expected, "Source archive contains missing or unexpected files")
        for name in required:
            member = source.extractfile(f"{stem}/{name}")
            require(
                member is not None and member.read() == (root / name).read_bytes(),
                f"Source archive differs: {name}",
            )
        metadata = source.extractfile(f"{stem}/PKG-INFO")
        require(metadata is not None, "Source archive metadata missing")
        assert metadata is not None
        check_metadata(metadata.read(), root, version)
    hashes = "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in sorted(expected_files)
    )
    manifest = directory / "SHA256SUMS"
    if manifest.exists():
        require(manifest.read_text() == hashes, "Distribution hashes changed after validation")
    else:
        manifest.write_text(hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="")
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = check_versions(root, args.tag)
    if args.dist is not None:
        check_distributions(root, args.dist, version)
    print(
        f"Validated {version}"
        + (" wheel/source contents and SHA256SUMS" if args.dist else " versions")
    )


if __name__ == "__main__":
    main()
