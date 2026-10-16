"""Minimal console automation for newly created VikingBot apps only.

Uses the Feishu developer-console agent template for new apps. No automatic replay
of non-idempotent create/publish calls. Checkpoints are saved before every such call.
"""

from pathlib import Path
from uuid import uuid4

from loguru import logger

from .web_session import SetupError, data, field

SCOPES = {
    "im:message.group_at_msg:readonly",
    "im:message:send_as_bot",
    "im:chat:read",
    "im:chat.members:read",
}
EVENT = "im.message.receive_v1"


def scope_ids(value, bucket=None, required=SCOPES):
    found = {}
    if isinstance(value, list):
        for item in value:
            found.update(scope_ids(item, bucket, required))
    elif isinstance(value, dict):
        name = field(value, "scope_name", "scopeName", "name", "key", "scopeKey")
        identifier = None
        for key in ("id", "scope_id", "scopeId", "scopeID"):
            raw = value.get(key)
            if isinstance(raw, str) and raw:
                identifier = raw
                break
            if isinstance(raw, int) and not isinstance(raw, bool):
                identifier = str(raw)
                break
        if name in required and identifier and bucket != "user":
            found[name] = identifier
        for key, child in value.items():
            next_bucket = "user" if "user" in key.lower() else bucket
            if any(word in key.lower() for word in ("app", "tenant", "client")):
                next_bucket = "tenant"
            if isinstance(child, (dict, list)):
                found.update(scope_ids(child, next_bucket, required))
    return found


def event_ids(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(event_ids(item) for item in value))
    if isinstance(value, dict):
        return event_ids(value.get("id", [])) | event_ids(value.get("items", []))
    return set()


async def create_app(session, run, checkpoint):
    if run.get("app_id"):
        return run["app_id"]
    if run.get("create_started"):
        raise SetupError("creation_uncertain")
    icon = Path(__file__).with_name("icon.png").read_bytes()
    uploaded = await session.post(
        "/developers/v1/app/upload/image",
        {"uploadType": "4", "isIsv": "false", "scale": '{"width":512,"height":512}'},
        files={"file": ("vikingbot.png", icon, "image/png")},
    )
    avatar = field(uploaded, "url")
    if not avatar:
        raise SetupError("icon_upload_failed")
    # Persist the request identity before creation; an uncertain result must never
    # fall back to app/create and silently create a second, ordinary bot.
    request_id = run.get("template_request_id") or str(uuid4())
    checkpoint(create_started=True, template_request_id=request_id)
    created = await session.post(
        "/developers/v1/manifest/upsert_by_template",
        {
            "appManifestTemplateID": "developer_console",
            "createAppUserCustomField": {
                "i18n": {
                    "zh_cn": {
                        "name": run["name"],
                        "description": "OpenViking AI assistant",
                    }
                },
                "avatar": avatar,
                "primaryLang": "zh_cn",
            },
            "cid": request_id,
            "HTTPHead": {},
        },
    )
    app_id = field(created, "ClientID", "clientID", "clientId", "appId")
    if not app_id or not app_id.startswith("cli_"):
        raise SetupError("creation_uncertain")
    checkpoint(app_id=app_id)
    return app_id


async def prepare_app(session, app_id):
    await session.post(
        f"/developers/v1/robot/switch/{app_id}", {"clientId": app_id, "enable": True}
    )
    await session.post(
        f"/developers/v1/event/switch/{app_id}", {"clientId": app_id, "eventMode": 4}
    )
    secret = field(await session.post(f"/developers/v1/secret/{app_id}"), "secret")
    if not secret:
        raise SetupError("credentials_unavailable")
    return secret


async def configure_app(session, app_id, require_mention=True):
    required = SCOPES if require_mention else SCOPES | {"im:message.group_msg"}
    catalog = scope_ids(await session.post(f"/developers/v1/scope/all/{app_id}"), required=required)
    if missing := required - catalog.keys():
        logger.warning(
            "Feishu permission catalog missing required scope names: {}", sorted(missing)
        )
        raise SetupError("permissions_unavailable")
    await session.post(
        f"/developers/v1/scope/update/{app_id}",
        {
            "clientId": app_id,
            "appScopeIDs": list(catalog.values()),
            "userScopeIDs": [],
            "scopeIds": [],
            "operation": "add",
            "isDeveloperPanel": True,
        },
    )
    await session.post(
        f"/developers/v1/event/update/{app_id}",
        {
            "clientId": app_id,
            "eventMode": 4,
            "operation": "add",
            "events": [],
            "appEvents": [EVENT],
            "userEvents": [],
        },
    )
    state = data(await session.post(f"/developers/v1/event/{app_id}"))
    events = set().union(
        *(
            event_ids(state.get(key, []))
            for key in (
                "events",
                "appEvents",
                "appEventDetails",
                "eventDetails",
            )
        )
    )
    if state.get("eventMode") != 4 or EVENT not in events:
        raise SetupError("events_not_ready")


async def publish_app(session, run, checkpoint):
    app_id = run["app_id"]
    if not run.get("version_id"):
        if run.get("version_started"):
            raise SetupError("publication_uncertain")
        checkpoint(version_started=True)
        visible = {
            "departments": [],
            "members": [run["owner"]["user_id"]],
            "groups": [],
            "isAll": 0,
        }
        created = await session.post(
            f"/developers/v1/app_version/create/{app_id}",
            {
                "appVersion": "1.0.0",
                "mobileDefaultAbility": "bot",
                "pcDefaultAbility": "bot",
                "changeLog": "Initial VikingBot release.",
                "visibleSuggest": visible,
                "blackVisibleSuggest": {**visible, "members": []},
            },
        )
        version_id = field(created, "versionId", "version_id", "id") or field(
            data(created).get("appVersion", {}), "versionId", "version_id", "id"
        )
        if not version_id:
            raise SetupError("publication_uncertain")
        checkpoint(version_id=version_id)
    if not run.get("publish_started"):
        checkpoint(publish_started=True)
        await session.post(
            f"/developers/v1/publish/commit/{app_id}/{run['version_id']}", {"clientId": app_id}
        )
    return await publication_status(session, run)


async def publication_status(session, run):
    versions = data(await session.post(f"/developers/v1/app_version/list/{run['app_id']}"))
    for version in versions.get("versions", []):
        if field(version, "versionId", "version_id", "id") == run["version_id"]:
            status = version.get("versionStatus")
            if status == 2:
                return "published"
            if status == 1:
                return "awaiting_approval"
    raise SetupError("publication_uncertain")
