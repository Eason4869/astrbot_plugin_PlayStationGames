import asyncio

import pytest


@pytest.mark.parametrize("message", [
    "帮我看看 Python 游戏开发教程", "这个游戏怎么样", "Steam 游戏库打不开",
    "把文件绑定到快捷键", "比较一下两款手机", "公司绩效排行榜",
    "我拿了一个奖杯，庆祝一下", "我觉得这个游戏怎么样", "PS 软件的图层怎么用",
])
def test_unrelated_chat_is_not_psn_intent(plugin_module, message):
    plugin = plugin_module.PlayStationGamesPlugin.__new__(plugin_module.PlayStationGamesPlugin)
    assert plugin._detect_intent(message) is None


@pytest.mark.parametrize(("message", "intent"), [
    ("查一下我的 PSN 资料", "profile"),
    ("看看我的奖杯", "trophies"),
    ("我的奖杯", "trophies"),
    ("看看小明的奖杯", "trophies"),
    ("小明的游戏库有哪些", "library"),
    ("群里谁最肝", "ranking"),
    ("我大镖客2玩了多久", "game"),
    ("老头环的游戏信息", "game"),
    ("老头环玩了多久", "game"),
    ("战神5奖杯进度", "game"),
    ("现在群里谁在线", "online"),
    ("绑定psn，ID是 XiaoMing", "bind"),
])
def test_documented_psn_requests_keep_working(plugin_module, message, intent):
    plugin = plugin_module.PlayStationGamesPlugin.__new__(plugin_module.PlayStationGamesPlugin)
    assert plugin._detect_intent(message) == intent


def test_llm_tool_ignores_unrelated_current_message(plugin_module):
    plugin = plugin_module.PlayStationGamesPlugin.__new__(plugin_module.PlayStationGamesPlugin)
    class Event:
        stopped = False
        def get_message_str(self):
            return "公司绩效排行榜"
        def stop_event(self):
            self.stopped = True
    event = Event()
    plugin._gate = lambda _: (True, "")
    async def collect():
        return [item async for item in plugin.tool_ranking(event)]
    results = asyncio.run(collect())
    assert results == []
    assert not event.stopped


def test_game_trophy_request_extracts_only_game_name(plugin_module):
    plugin = plugin_module.PlayStationGamesPlugin.__new__(plugin_module.PlayStationGamesPlugin)
    assert plugin._extract_game_keyword("战神5奖杯进度") == "战神5"
