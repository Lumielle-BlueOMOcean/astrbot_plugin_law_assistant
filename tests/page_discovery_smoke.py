from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


async def discover(core_source: Path, plugin_root: Path) -> list[str]:
    sys.path.insert(0, str(core_source.resolve()))
    from astrbot.core.star.star import StarMetadata
    from astrbot.dashboard.routes.plugin import PluginRoute

    manager = SimpleNamespace(
        plugin_store_path=str(plugin_root.parent), reserved_plugin_path=""
    )
    route = PluginRoute.__new__(PluginRoute)
    route.plugin_manager = manager
    metadata = StarMetadata(
        name="astrbot_plugin_law_assistant",
        root_dir_name=plugin_root.name,
        reserved=False,
    )
    pages = await route._discover_plugin_pages(metadata)
    return [page.name for page in pages]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("core_source", type=Path)
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    pages = asyncio.run(discover(args.core_source, args.plugin_root.resolve()))
    if "law-assistant" not in pages:
        raise SystemExit(f"Plugin Page discovery failed: {pages}")
    print(f"Plugin Page discovery PASS: {pages}")


if __name__ == "__main__":
    main()
