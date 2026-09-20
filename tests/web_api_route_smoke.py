from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_NAME = "astrbot_plugin_law_assistant"
EXPECTED_ROUTES = {
    ("overview", "GET"),
    ("library/search", "GET"),
    ("radar/scan", "POST"),
    ("files/stage", "POST"),
    ("plans/prepare", "POST"),
}


def run_smoke(core_source: Path, plugin_root: Path) -> None:
    sys.path.insert(0, str(core_source.resolve()))
    sys.path.insert(0, str(plugin_root.resolve()))
    from astrbot.dashboard.server import _match_registered_web_api

    from web_api import LawAssistantWebApi

    context = SimpleNamespace(registered_web_apis=[])
    context.register_web_api = lambda route, handler, methods, desc: (
        context.registered_web_apis.append((route, handler, methods, desc))
    )
    LawAssistantWebApi(object()).register(context)

    if not context.registered_web_apis:
        raise AssertionError("没有注册 Web API")
    if any(
        not route.startswith(f"/{PLUGIN_NAME}/")
        for route, *_ in context.registered_web_apis
    ):
        raise AssertionError("存在未带插件 namespace 的 Web API route")

    for suffix, method in EXPECTED_ROUTES:
        matched = _match_registered_web_api(
            context.registered_web_apis,
            f"{PLUGIN_NAME}/{suffix}",
            method,
        )
        if matched is None:
            raise AssertionError(f"route contract 未匹配：{method} {suffix}")

    print(
        "Web API route contract PASS: "
        f"plugin={PLUGIN_NAME} routes={len(context.registered_web_apis)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("core_source", type=Path)
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    run_smoke(args.core_source, args.plugin_root)


if __name__ == "__main__":
    main()
