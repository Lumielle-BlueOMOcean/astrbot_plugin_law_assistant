"""Validate installable plugin archives against the deep Windows path budget."""

from __future__ import annotations

import argparse
import json
import ntpath
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from zipfile import ZipFile

PLUGIN_NAME = "astrbot_plugin_law_assistant"
DEFAULT_WINDOWS_INSTALL_ROOT = (
    r"E:\Astrbot\Astrbot to Wechat\AstrBotLauncher-0.3.0"
    r"\AstrBotLauncher-0.3.0\AstrBot\data\plugins\astrbot_plugin_law_assistant"
)
WINDOWS_PATH_BUDGET = 240
WINDOWS_TERMINATOR_UNITS = 1
WINDOWS_SAFETY_MARGIN_UNITS = 8
REQUIRED_RUNTIME_FILES = {
    "README.md",
    "metadata.yaml",
    "main.py",
    "_conf_schema.json",
    "requirements.txt",
    "service.py",
    "storage.py",
    "sources/__init__.py",
    "pages/law-assistant/index.html",
}
DEVELOPMENT_ROOTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".cache",
    "build",
    "cache",
    "dist",
    "docs",
    "scripts",
    "tests",
    "venv",
    "logs",
    "log",
    "tmp",
    "__pycache__",
}
DEVELOPMENT_ROOT_FILES = {"AGENTS.md", ".gitignore", ".gitattributes"}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".db-shm",
    ".db-wal",
    ".tmp",
}
SECRET_NAMES = {
    "api-key",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "private-key",
    "private_key",
    "secret",
    "token",
}


@dataclass(frozen=True)
class ArchiveReport:
    distribution: str
    root_directory: str
    entry_count: int
    file_count: int
    development_file_count: int
    longest_member: str
    longest_absolute_path: str
    longest_absolute_path_chars: int
    longest_absolute_path_utf16_units: int
    budgeted_path_utf16_units: int
    over_budget_count: int
    missing_required_files: tuple[str, ...]


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _clean_member(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe ZIP member path: {name}")
    return normalized.rstrip("/")


def analyze_archive(
    archive_path: str,
    *,
    distribution: str,
    install_root: str = DEFAULT_WINDOWS_INSTALL_ROOT,
) -> ArchiveReport:
    if distribution not in {"source", "release"}:
        raise ValueError("distribution must be 'source' or 'release'")

    with ZipFile(archive_path) as archive:
        infos = archive.infolist()
        members = [_clean_member(info.filename) for info in infos if not info.is_dir()]
    if not members:
        raise ValueError("archive has no files")

    roots = {PurePosixPath(member).parts[0] for member in members}
    if len(roots) != 1:
        raise ValueError(f"archive must have one root directory, found {sorted(roots)}")
    root = roots.pop()

    if distribution == "release" and root != PLUGIN_NAME:
        raise ValueError(f"release ZIP root must be {PLUGIN_NAME!r}, got {root!r}")
    if distribution == "source" and not re.fullmatch(
        rf"{re.escape(PLUGIN_NAME)}-[0-9a-fA-F]{{7,40}}", root
    ):
        raise ValueError(f"source ZIP root must contain a commit SHA, got {root!r}")

    relative_members = ["/".join(PurePosixPath(member).parts[1:]) for member in members]
    missing = tuple(sorted(REQUIRED_RUNTIME_FILES - set(relative_members)))
    development_files = sum(
        bool(PurePosixPath(member).parts)
        and (
            PurePosixPath(member).parts[0] in DEVELOPMENT_ROOTS
            or member in DEVELOPMENT_ROOT_FILES
            or PurePosixPath(member).name.lower() in SECRET_NAMES
            or PurePosixPath(member).suffix.lower() in FORBIDDEN_SUFFIXES
            or any(
                part.lower().startswith(".env") for part in PurePosixPath(member).parts
            )
        )
        for member in relative_members
    )

    path_pairs = [
        (
            member,
            ntpath.join(install_root, member.replace("/", "\\")),
        )
        for member in members
    ]
    longest_member, longest_path = max(
        path_pairs,
        key=lambda item: _utf16_units(item[1]),
    )
    longest_units = _utf16_units(longest_path)
    budgeted_units = (
        longest_units + WINDOWS_TERMINATOR_UNITS + WINDOWS_SAFETY_MARGIN_UNITS
    )
    over_budget = sum(
        _utf16_units(path) + WINDOWS_TERMINATOR_UNITS + WINDOWS_SAFETY_MARGIN_UNITS
        > WINDOWS_PATH_BUDGET
        for _, path in path_pairs
    )

    return ArchiveReport(
        distribution=distribution,
        root_directory=root,
        entry_count=len(infos),
        file_count=len(members),
        development_file_count=development_files,
        longest_member=longest_member,
        longest_absolute_path=longest_path,
        longest_absolute_path_chars=len(longest_path),
        longest_absolute_path_utf16_units=longest_units,
        budgeted_path_utf16_units=budgeted_units,
        over_budget_count=over_budget,
        missing_required_files=missing,
    )


def validate_archive(
    archive_path: str,
    *,
    distribution: str,
    expected_sha: str | None = None,
    install_root: str = DEFAULT_WINDOWS_INSTALL_ROOT,
) -> ArchiveReport:
    report = analyze_archive(
        archive_path,
        distribution=distribution,
        install_root=install_root,
    )
    errors = []
    if expected_sha and report.root_directory != f"{PLUGIN_NAME}-{expected_sha}":
        errors.append(
            f"source archive root SHA mismatch: {report.root_directory!r} "
            f"!= {PLUGIN_NAME}-{expected_sha!s}"
        )
    if report.missing_required_files:
        errors.append(
            "archive missing required runtime files: "
            + ", ".join(report.missing_required_files)
        )
    if report.development_file_count:
        errors.append(
            f"archive contains {report.development_file_count} development files"
        )
    if report.over_budget_count:
        errors.append(
            "Windows path budget exceeded: "
            f"{report.over_budget_count} member(s); longest path is "
            f"{report.longest_absolute_path_chars} characters plus terminator/margin "
            f"({report.budgeted_path_utf16_units}/{WINDOWS_PATH_BUDGET} UTF-16 units)"
        )
    if errors:
        raise ValueError("; ".join(errors))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="ZIP archive to inspect")
    parser.add_argument("--distribution", choices=("source", "release"), required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--install-root", default=DEFAULT_WINDOWS_INSTALL_ROOT)
    args = parser.parse_args()
    try:
        report = validate_archive(
            args.archive,
            distribution=args.distribution,
            expected_sha=args.expected_sha,
            install_root=args.install_root,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
