from pathlib import Path
from zipfile import ZipFile

from scripts.archive_validation import validate_archive


def test_release_builder_excludes_development_and_runtime_files(tmp_path):
    from scripts.build_release import build_release

    output = build_release(Path(__file__).parents[1], tmp_path)
    assert output.name == "astrbot_plugin_law_assistant-0.6.1.zip"
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        first_member = archive.namelist()[0]
        metadata = archive.read("astrbot_plugin_law_assistant/metadata.yaml").decode(
            "utf-8"
        )

    assert first_member == "astrbot_plugin_law_assistant/"
    assert "astrbot_plugin_law_assistant/metadata.yaml" in names
    assert 'version: "0.6.1"' in metadata
    assert "astrbot_plugin_law_assistant/structured_ingestion.py" in names
    assert "astrbot_plugin_law_assistant/question_session.py" in names
    assert "astrbot_plugin_law_assistant/question_session_repository.py" in names
    assert "astrbot_plugin_law_assistant/pages/law-assistant/index.html" in names
    assert "astrbot_plugin_law_assistant/AGENTS.md" not in names
    assert "astrbot_plugin_law_assistant/.gitignore" not in names
    assert "astrbot_plugin_law_assistant/.gitattributes" not in names
    assert all(
        not name.startswith("astrbot_plugin_law_assistant/tests/") for name in names
    )
    assert all(
        not name.startswith("astrbot_plugin_law_assistant/.github/") for name in names
    )
    assert not any(
        name.endswith((".sqlite3", ".db", ".log", ".pyc")) or "__pycache__" in name
        for name in names
    )
    assert not any(
        name.endswith((".pdf", ".json")) and "structured" in name.lower()
        for name in names
    )
    report = validate_archive(output, distribution="release")
    assert report.over_budget_count == 0
