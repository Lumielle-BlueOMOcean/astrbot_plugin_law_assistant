from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
import tempfile
from pathlib import Path

MODULE_NAME = "data.plugins.astrbot_plugin_law_assistant.main"


def run_smoke(
    core_source: Path,
    expected_commit: str | None = None,
    plugin_root: Path | None = None,
) -> None:
    core_source = core_source.resolve()
    if not (core_source / "astrbot").is_dir():
        raise SystemExit(f"AstrBot source directory is missing astrbot/: {core_source}")

    repository = (plugin_root or Path(__file__).parents[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="law-assistant-compat-") as temp:
        runtime_root = Path(temp)
        os.environ["ASTRBOT_ROOT"] = str(runtime_root)
        data_package = runtime_root / "data"
        plugins_package = data_package / "plugins"
        plugins_package.mkdir(parents=True)
        (data_package / "__init__.py").write_text("", encoding="utf-8")
        (plugins_package / "__init__.py").write_text("", encoding="utf-8")
        plugin_destination = plugins_package / "astrbot_plugin_law_assistant"
        if plugin_root is None:
            plugin_destination.symlink_to(repository, target_is_directory=True)
        else:
            import shutil

            shutil.copytree(repository, plugin_destination)

        sys.path.insert(0, str(runtime_root))
        sys.path.insert(0, str(core_source))
        importlib.import_module("astrbot")
        module = importlib.import_module(MODULE_NAME)

        from astrbot.core.provider.register import llm_tools
        from astrbot.core.star import StarTools, star_map
        from astrbot.core.star.star_handler import star_handlers_registry
        from astrbot.core.star.star_manager import PluginManager

        loaded_metadata = PluginManager._load_plugin_metadata(str(repository))
        if (
            loaded_metadata is None
            or loaded_metadata.name != "astrbot_plugin_law_assistant"
        ):
            raise AssertionError("AstrBot loader did not parse metadata.yaml")
        if (
            PluginManager._get_plugin_dir_name_from_metadata(str(repository))
            != "astrbot_plugin_law_assistant"
        ):
            raise AssertionError("AstrBot loader rejected the plugin directory name")
        compatible, reason = PluginManager._validate_astrbot_version_specifier(
            loaded_metadata.astrbot_version,
        )
        if not compatible:
            raise AssertionError(f"AstrBot version range rejected: {reason}")

        metadata = star_map.get(MODULE_NAME)
        if metadata is None or metadata.star_cls_type is None:
            raise AssertionError("AstrBot did not register the plugin Star class")

        tools = {tool.name for tool in llm_tools.func_list}
        required_tools = {
            "law_status",
            "law_scan_events",
            "law_list_events",
            "law_get_event",
            "law_list_deadlines",
            "law_get_daily_case",
            "law_generate_question",
            "law_study_question",
            "law_question_session",
            "law_question_inventory",
            "law_archive_learning_material",
            "law_import_learning_document",
            "law_search_learning_library",
            "law_get_learning_item",
            "law_update_learning_item",
            "law_list_targets",
            "law_rename_target",
            "law_get_daily_plans",
            "law_prepare_publish_question",
            "law_prepare_publish_case",
            "law_prepare_daily_plan_update",
            "law_confirm_daily_plan_update",
            "law_list_law_updates",
            "law_prepare_publish_event",
            "law_confirm_publish",
        }
        if not required_tools.issubset(tools):
            raise AssertionError(f"Missing LLM tools: {required_tools - tools}")

        handlers = star_handlers_registry.get_handlers_by_module_name(MODULE_NAME)
        if not any(handler.handler_name == "law" for handler in handlers):
            raise AssertionError("AstrBot did not register the /law command handler")

        StarTools.initialize(object())
        plugin = metadata.star_cls_type(
            context=object(), config={"auto_scan_enabled": False}
        )
        asyncio.run(plugin.initialize())
        asyncio.run(plugin.terminate())

        print(
            f"compatibility smoke PASS: commit={expected_commit or 'unspecified'} "
            f"module={module.__name__} handlers={len(handlers)} tools={sorted(required_tools)}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Law Assistant against real AstrBot source"
    )
    parser.add_argument("core_source", type=Path)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--plugin-root", type=Path, default=None)
    args = parser.parse_args()
    run_smoke(args.core_source, args.commit, args.plugin_root)


if __name__ == "__main__":
    main()
