from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.archive_validation import (
    DEFAULT_WINDOWS_INSTALL_ROOT,
    analyze_archive,
    validate_archive,
)

ROOT = Path(__file__).parents[1]
SHA = "d20349045ded1ebc25645d3be1c163ca20114d10"


def _write_archive(path: Path, members: dict[str, str]) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def _required_source_members(root: str) -> dict[str, str]:
    return {
        f"{root}/main.py": "",
        f"{root}/metadata.yaml": 'version: "0.6.1"\n',
        f"{root}/_conf_schema.json": "{}",
        f"{root}/requirements.txt": "",
        f"{root}/README.md": "",
        f"{root}/service.py": "",
        f"{root}/storage.py": "",
        f"{root}/sources/__init__.py": "",
        f"{root}/pages/law-assistant/index.html": "",
    }


def test_old_deep_source_archive_exceeds_windows_budget_and_sha_counts(tmp_path):
    root = f"astrbot_plugin_law_assistant-{SHA}"
    members = _required_source_members(root)
    offender = (
        f"{root}/docs/superpowers/plans/2026-09-17-law-assistant-storage-hardening.md"
    )
    members[offender] = "design history"
    archive_path = _write_archive(tmp_path / "old-source.zip", members)

    report = analyze_archive(
        archive_path,
        distribution="source",
        install_root=DEFAULT_WINDOWS_INSTALL_ROOT,
    )

    assert report.longest_member == offender
    assert report.longest_absolute_path_chars > 240
    assert report.over_budget_count >= 1
    with pytest.raises(ValueError, match="Windows path budget"):
        validate_archive(archive_path, distribution="source")


def test_archive_budget_includes_sha_root_and_changes_with_sha_length(tmp_path):
    short_root = "astrbot_plugin_law_assistant-1234567"
    long_root = f"astrbot_plugin_law_assistant-{SHA}"
    relative = "main.py"
    short = _write_archive(tmp_path / "short.zip", {f"{short_root}/{relative}": ""})
    long = _write_archive(tmp_path / "long.zip", {f"{long_root}/{relative}": ""})

    short_report = analyze_archive(short, distribution="source")
    long_report = analyze_archive(long, distribution="source")

    assert (
        long_report.longest_absolute_path_chars
        - short_report.longest_absolute_path_chars
        == len(SHA) - 7
    )


def test_required_runtime_files_are_checked_for_source_and_release(tmp_path):
    source_root = f"astrbot_plugin_law_assistant-{SHA}"
    source = _write_archive(
        tmp_path / "missing-source.zip", {f"{source_root}/main.py": ""}
    )
    release = _write_archive(
        tmp_path / "missing-release.zip", {"astrbot_plugin_law_assistant/main.py": ""}
    )

    with pytest.raises(ValueError, match="required runtime files"):
        validate_archive(source, distribution="source")
    with pytest.raises(ValueError, match="required runtime files"):
        validate_archive(release, distribution="release")


def test_release_archive_layout_and_runtime_files_fit_deep_install_root(tmp_path):
    root = "astrbot_plugin_law_assistant"
    archive_path = _write_archive(
        tmp_path / "release.zip",
        _required_source_members(root),
    )

    report = validate_archive(archive_path, distribution="release")

    assert report.root_directory == root
    assert report.over_budget_count == 0


def test_git_archive_obeys_export_ignore_in_worktree_attributes(tmp_path):
    archive_path = tmp_path / "git-archive.zip"
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "archive",
            "--worktree-attributes",
            "--format=zip",
            f"--prefix=astrbot_plugin_law_assistant-{commit}/",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    archive_path.write_bytes(result.stdout)

    report = validate_archive(archive_path, distribution="source")

    assert report.development_file_count == 0
    assert report.over_budget_count == 0


def test_ci_checks_real_exact_commit_source_and_metadata_versioned_package():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "archive/$SOURCE_ARCHIVE_SHA.zip" in workflow
    assert "scripts/archive_validation.py" in workflow
    assert "tests/source_archive_install_smoke.py" in workflow
    assert "--distribution release" in workflow
    assert "--astrbot-commit" in workflow
    assert "steps.plugin-version.outputs.version" in workflow
    assert "astrbot_plugin_law_assistant-0.6.0.zip" not in workflow
    assert "astrbot_plugin_law_assistant-0.6.0\n" not in workflow
