import asyncio


def plugin(plugin_module, enabled):
    instance = plugin_module.PlayStationGamesPlugin.__new__(plugin_module.PlayStationGamesPlugin)
    instance._enabled_groups = set(enabled)
    instance._disabled_groups = set()
    instance.npsso_token = "configured"
    instance._log_usage = lambda *args: None
    instance._save_enabled_groups = lambda: None
    return instance


class Event:
    def __init__(self, group_id):
        self.group_id = group_id
    def get_group_id(self):
        return self.group_id
    def plain_result(self, text):
        return text


def run(handler):
    async def collect():
        return [item async for item in handler]
    return asyncio.run(collect())


def test_disable_from_all_groups_mode(plugin_module):
    instance = plugin(plugin_module, [])
    run(instance.cmd_disable(Event("123")))
    assert not instance._group_enabled("123")
    assert instance._group_enabled("456")
    run(instance.cmd_enable(Event("123")))
    assert instance._group_enabled("123")
    assert instance._group_enabled("456")


def test_disable_last_whitelisted_group_does_not_open_all(plugin_module):
    instance = plugin(plugin_module, ["123"])
    run(instance.cmd_disable(Event("123")))
    assert not instance._group_enabled("123")
    assert not instance._group_enabled("456")


def test_disabled_group_survives_reload(plugin_module, tmp_path):
    instance = plugin(plugin_module, [])
    instance.enabled_groups_file = tmp_path / "enabled_groups.json"
    instance._save_enabled_groups = plugin_module.PlayStationGamesPlugin._save_enabled_groups.__get__(instance)
    run(instance.cmd_disable(Event("123")))

    reloaded = plugin(plugin_module, [])
    reloaded.enabled_groups_file = instance.enabled_groups_file
    reloaded._load_enabled_groups()
    assert not reloaded._group_enabled("123")
    assert reloaded._group_enabled("456")
