"""Build a clean AstrBot plugin archive from tracked repository files."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PLUGIN_NAME = "astrbot_plugin_law_assistant"
_VERSION_RE = re.compile(r'^version:\s*["\']?([^"\'\s]+)', re.MULTILINE)
_EXCLUDED_PREFIXES = (
    ".git/",
    ".github/",
    ".venv/",
    "venv/",
    "tests/",
    "scripts/",
    "docs/",
    ".pytest_cache/",
    ".ruff_cache/",
    "__pycache__/",
    "dist/",
    "build/",
)
_EXCLUDED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".tmp",
    ".sqlite-wal",
    ".sqlite-shm",
    ".db-wal",
    ".db-shm",
)
_SECRET_NAMES = {
    ".env",
    "credentials",
    "credential",
    "token",
    "private_key",
    "private-key",
    "secret",
}


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [root / item for item in result.stdout.decode().split("\0") if item]


def _is_excluded(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    if normalized.startswith(_EXCLUDED_PREFIXES):
        return True
    if normalized.endswith(_EXCLUDED_SUFFIXES):
        return True
    parts = set(normalized.lower().split("/"))
    if parts & _SECRET_NAMES:
        return True
    return any(part.startswith(".env") for part in parts)


def _version(root: Path) -> str:
    metadata = (root / "metadata.yaml").read_text(encoding="utf-8")
    match = _VERSION_RE.search(metadata)
    if match is None:
        raise ValueError("metadata.yaml 缺少 version")
    return match.group(1)


def build_release(root: str | Path, output_dir: str | Path) -> Path:
    root_path = Path(root).resolve()
    output_path = Path(output_dir).resolve()
    version = _version(root_path)
    files = []
    for path in _tracked_files(root_path):
        relative = path.relative_to(root_path).as_posix()
        if not path.is_file() or _is_excluded(relative):
            continue
        files.append((relative, path))
    required = {"metadata.yaml", "main.py", "_conf_schema.json", "requirements.txt"}
    included = {relative for relative, _ in files}
    missing = required - included
    if missing:
        raise ValueError(f"安装包缺少必要文件：{sorted(missing)}")
    output_path.mkdir(parents=True, exist_ok=True)
    archive_path = output_path / f"{PLUGIN_NAME}-{version}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for relative, path in sorted(files):
            archive.write(path, f"{PLUGIN_NAME}/{relative}")
    if archive_path.stat().st_size <= 0:
        raise ValueError("安装包为空")
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    print(build_release(args.root, args.output))


if __name__ == "__main__":
    main()
