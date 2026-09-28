import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def plugin_module(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("psn_plugin")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, "psn_plugin", package)

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")

    def decorator(*args, **kwargs):
        return lambda func: func

    api.logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                                       error=lambda *a, **k: None, debug=lambda *a: None)
    event.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event.filter = types.SimpleNamespace(
        command=decorator, regex=decorator, llm_tool=decorator,
        permission_type=decorator, PermissionType=types.SimpleNamespace(ADMIN=1),
    )
    star.Context = type("Context", (), {})
    star.Star = type("Star", (), {})
    star.StarTools = type("StarTools", (), {})
    star.register = decorator
    for name, module in (("astrbot", astrbot), ("astrbot.api", api),
                         ("astrbot.api.event", event), ("astrbot.api.star", star)):
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("psn_plugin.main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module
