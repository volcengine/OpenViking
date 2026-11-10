"""Opt-in real OV/Bot parser contract. Only a temporary fixture file is read."""

import json
import sys
import tempfile
from pathlib import Path

from vikingbot.config import loader

from openviking.config.config_file import _validate


def check(document):
    config = _validate(json.dumps(document))
    assert config.vlm.provider == "openai"
    assert config.vlm.model == "smoke/chat"
    assert config.vlm.max_tokens == document["vlm"].get("max_tokens")
    with tempfile.TemporaryDirectory(prefix="ov-models-contract-") as directory:
        file = Path(directory) / "ov.conf"
        file.write_text(json.dumps(document))
        loader.CONFIG_PATH = file
        bot = loader.load_config()
        assert bot.agents.max_tokens == document.get("bot", {}).get("agents", {}).get("maxTokens")
        return bot


document = json.loads(sys.stdin.read())
bot = check(document)
assert bot.inherits_root_vlm()
assert bot.agents.model == "smoke/chat"
assert bot.get_root_vlm_config().credentials[0].api_base.endswith("/v1")
document["bot"]["agents"].update(
    model="smoke/bot", provider="openai", apiKey="fake", apiBase="http://127.0.0.1:3425/v1"
)
bot = check(document)
assert not bot.inherits_root_vlm()
assert bot.agents.model == "smoke/bot"
print("PASS: real OV startup validation, full Magpie IDs, Bot follow and independent selection")
