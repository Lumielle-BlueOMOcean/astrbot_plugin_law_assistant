"""Install a source or release ZIP using AstrBot's real archive updater."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]


def run_smoke(
    core_source: Path,
    archive_path: Path,
    distribution: str,
    astrbot_commit: str,
    output_root: Path,
    expected_version: str,
    source_commit: str | None = None,
) -> None:
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    from scripts.archive_validation import PLUGIN_NAME, validate_archive

    report = validate_archive(
        str(archive_path),
        distribution=distribution,
        expected_sha=source_commit,
    )
    expected_root = (
        f"{PLUGIN_NAME}-{source_commit}" if distribution == "source" else PLUGIN_NAME
    )
    if report.root_directory != expected_root:
        raise AssertionError(
            f"archive root {report.root_directory!r} != {expected_root!r}"
        )

    core_source = core_source.resolve()
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"output root must be absent or empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(core_source))
    from astrbot.core.star.updator import PluginUpdator

    with tempfile.TemporaryDirectory(prefix="law-source-archive-") as temp:
        temp_root = Path(temp)
        copied_zip = temp_root / "source.zip"
        shutil.copyfile(archive_path, copied_zip)
        # Exercise the exact unzip/root-normalization method from the selected AstrBot source.
        updater = PluginUpdator.__new__(PluginUpdator)
        updater.unzip_file(str(copied_zip), str(output_root))

    required = (
        "main.py",
        "metadata.yaml",
        "_conf_schema.json",
        "requirements.txt",
        "README.md",
        "service.py",
        "sources/__init__.py",
        "pages/law-assistant/index.html",
    )
    missing = [
        relative for relative in required if not (output_root / relative).is_file()
    ]
    if missing:
        raise AssertionError(
            f"AstrBot source archive installation is incomplete: {missing}"
        )
    metadata = (output_root / "metadata.yaml").read_text(encoding="utf-8")
    version_match = re.search(r'^version:\s*["\']?([^"\'\s]+)', metadata, re.MULTILINE)
    if version_match is None or version_match.group(1) != expected_version:
        raise AssertionError(
            f"installed metadata version {version_match.group(1) if version_match else None!r} "
            f"!= {expected_version!r}"
        )
    if any(path.name.startswith(f"{PLUGIN_NAME}-") for path in output_root.iterdir()):
        raise AssertionError("SHA archive root was not normalized into plugin root")
    if (output_root / PLUGIN_NAME).exists():
        raise AssertionError(
            "plugin files remain nested below a duplicate plugin directory"
        )
    if (output_root / "docs").exists() or (output_root / "tests").exists():
        raise AssertionError("development directories remained in installed plugin")

    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(output_root)],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("compatibility_smoke.py")),
            str(core_source),
            "--commit",
            astrbot_commit,
            "--plugin-root",
            str(output_root),
        ],
        check=True,
    )
    print(
        f"plugin archive install smoke PASS: AstrBot source={core_source} "
        f"commit={astrbot_commit} archive={distribution} "
        f"archive_commit={source_commit or 'release'} plugin_root={output_root}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core_source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--distribution", choices=("source", "release"), required=True)
    parser.add_argument("--astrbot-commit", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.distribution == "source" and not args.source_commit:
        parser.error("--source-commit is required for source archives")
    if args.distribution == "release" and args.source_commit:
        parser.error("--source-commit is only valid for source archives")
    run_smoke(
        args.core_source,
        args.archive,
        args.distribution,
        args.astrbot_commit,
        args.output_root,
        args.version,
        args.source_commit,
    )


if __name__ == "__main__":
    main()
