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
