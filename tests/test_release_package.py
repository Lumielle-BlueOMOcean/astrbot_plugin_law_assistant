from pathlib import Path
from zipfile import ZipFile


def test_release_builder_excludes_development_and_runtime_files(tmp_path):
    from scripts.build_release import build_release

    output = build_release(Path(__file__).parents[1], tmp_path)
    assert output.name == "astrbot_plugin_law_assistant-0.4.0.zip"
    with ZipFile(output) as archive:
        names = set(archive.namelist())

    assert "astrbot_plugin_law_assistant/metadata.yaml" in names
    assert "astrbot_plugin_law_assistant/structured_ingestion.py" in names
    assert "astrbot_plugin_law_assistant/pages/law-assistant/index.html" in names
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
