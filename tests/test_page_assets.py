import json
import re
from pathlib import Path

PAGE_ROOT = Path(__file__).parents[1] / "pages" / "law-assistant"


def test_plugin_page_assets_are_self_contained_and_safe():
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    css = (PAGE_ROOT / "style.css").read_text(encoding="utf-8")

    assert 'src="app.js"' in html
    assert 'href="style.css"' in html
    assert "cdn." not in html + javascript + css
    assert "localStorage" not in javascript
    assert "document.cookie" not in javascript
    assert "window.AstrBotPluginPage" in javascript
    assert "textContent" in javascript
    assert "../" not in html + javascript + css


def test_plugin_page_exposes_all_0_3d_management_entrypoints():
    javascript = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    for endpoint in (
        "library/item",
        "library/update",
        "reviews",
        "review/status",
        "plans/prepare",
        "plans/confirm",
        "plans/reset-prepare",
        "plans/reset-confirm",
        "target/rename",
        "target/unbind-prepare",
        "target/unbind-confirm",
        "history/daily",
        "history/publications",
        "history/reminders",
        "history/sources",
    ):
        assert endpoint in javascript
    assert "bridge.t(" in javascript
    assert "window.confirm" not in javascript


def _resolve(messages, locale, key, fallback):
    for candidate in (locale, "zh-CN", "en-US"):
        current = messages.get(candidate)
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return str(current)
    return fallback


def test_plugin_page_i18n_uses_verified_page_namespace_and_locale_refresh():
    javascript = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    messages = {
        locale: json.loads(
            (
                PAGE_ROOT.parents[1] / ".astrbot-plugin" / "i18n" / f"{locale}.json"
            ).read_text(encoding="utf-8")
        )
        for locale in ("zh-CN", "en-US")
    }
    keys = sorted(set(re.findall(r'\bt\("([^"]+)"', javascript)))

    assert 'const PAGE_I18N_NAMESPACE = "pages.law-assistant.";' in javascript
    assert "return bridge.t(pageKey, fallback);" in javascript
    assert keys
    for key in keys:
        assert _resolve(messages, "zh-CN", f"pages.law-assistant.{key}", "")
        assert _resolve(messages, "en-US", f"pages.law-assistant.{key}", "")
    assert (
        _resolve(messages, "zh-CN", "pages.law-assistant.page.nav.overview", "")
        == "总览"
    )
    assert (
        _resolve(messages, "en-US", "pages.law-assistant.page.nav.overview", "")
        == "Overview"
    )
    assert (
        _resolve(messages, "en-US", "pages.law-assistant.page.missing", "Fallback")
        == "Fallback"
    )
    assert "if (pageInitialized) renderRoute().catch" in javascript
