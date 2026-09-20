from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
COMMIT_422 = "81c7b0f7150485beb6124a7ec524a8d8534e7f6e"
COMMIT_425 = "02291a3217c92faa0c577bf9d89076949c40954c"


def test_compatibility_smoke_is_source_based_and_does_not_stub_astrbot() -> None:
    smoke = (ROOT / "tests" / "compatibility_smoke.py").read_text()

    assert 'importlib.import_module("astrbot")' in smoke
    assert 'sys.modules["astrbot"]' not in smoke
    assert "data.plugins.astrbot_plugin_law_assistant.main" in smoke
    assert "--plugin-root" in smoke


def test_page_discovery_smoke_uses_real_astrbot_page_discovery() -> None:
    smoke = (ROOT / "tests" / "page_discovery_smoke.py").read_text()
    assert "PluginRoute" in smoke
    assert "core_source" in smoke
    assert "_discover_plugin_pages" in smoke
    assert "Path(...).exists" not in smoke


def test_ci_contains_both_required_exact_commits() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert COMMIT_422 in workflow
    assert COMMIT_425 in workflow
    assert "continue-on-error" not in workflow
