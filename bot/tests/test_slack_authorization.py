from vikingbot.bus.queue import MessageBus
from vikingbot.channels.slack import SlackChannel
from vikingbot.config.schema import ChannelsConfig, SlackChannelConfig, SlackDMConfig


def _slack_channel(config: SlackChannelConfig) -> SlackChannel:
    return SlackChannel(config, MessageBus())


def test_slack_defaults_fail_closed_for_dms_and_groups():
    config = SlackChannelConfig()
    channel = _slack_channel(config)

    assert config.dm.policy == "allowlist"
    assert config.group_policy == "allowlist"
    assert channel._is_allowed("U1", "D1", "im") is False
    assert channel._is_allowed("U1", "C1", "channel") is False


def test_slack_dm_allowlist_is_sender_scoped():
    channel = _slack_channel(
        SlackChannelConfig(
            dm=SlackDMConfig(policy="allowlist", allow_from=["U1"]),
        )
    )

    assert channel._is_allowed("U1", "D1", "im") is True
    assert channel._is_allowed("U2", "D1", "im") is False


def test_slack_group_allowlist_requires_channel_and_sender():
    channel = _slack_channel(
        SlackChannelConfig(
            group_policy="allowlist",
            group_allow_from=["C1"],
            group_sender_allow_from=["U1"],
        )
    )

    assert channel._is_allowed("U1", "C1", "channel") is True
    assert channel._is_allowed("U2", "C1", "channel") is False
    assert channel._is_allowed("U1", "C2", "channel") is False


def test_slack_explicit_open_and_mention_policies_allow_group_senders():
    for policy in ("open", "mention"):
        channel = _slack_channel(SlackChannelConfig(group_policy=policy))

        assert channel._is_allowed("U1", "C1", "channel") is True


def test_slack_camel_case_allowlists_are_normalized():
    channels = ChannelsConfig(
        channels=[
            {
                "type": "slack",
                "groupPolicy": "allowlist",
                "groupAllowFrom": ["C1"],
                "groupSenderAllowFrom": ["U1"],
                "dm": {"policy": "allowlist", "allowFrom": ["U2"]},
            }
        ]
    )

    config = channels.get_all_channels()[0]
    assert isinstance(config, SlackChannelConfig)
    assert config.group_allow_from == ["C1"]
    assert config.group_sender_allow_from == ["U1"]
    assert config.dm.allow_from == ["U2"]


async def test_slack_message_pipeline_uses_slack_authorization_policy():
    bus = MessageBus()
    channel = SlackChannel(
        SlackChannelConfig(
            group_policy="allowlist",
            group_allow_from=["C1"],
            group_sender_allow_from=["U1"],
        ),
        bus,
    )
    metadata = {"slack": {"channel_type": "channel"}}

    await channel._handle_message(
        sender_id="U1",
        chat_id="C1",
        content="allowed",
        metadata=metadata,
    )
    await channel._handle_message(
        sender_id="U2",
        chat_id="C1",
        content="blocked",
        metadata=metadata,
    )

    assert bus.inbound_size == 1
    assert (await bus.consume_inbound()).sender_id == "U1"
