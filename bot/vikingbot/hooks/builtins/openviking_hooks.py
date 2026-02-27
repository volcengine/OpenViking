from typing import Any

from loguru import logger

from ..base import Hook, HookContext
from ...session import Session

try:
    from vikingbot.openviking_mount.ov_server import VikingClient
    import openviking as ov
    HAS_OPENVIKING = True
except Exception:
    HAS_OPENVIKING = False
    VikingClient = None
    ov = None


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    def __init__(self):
        self._client = None

    async def _get_client(self, session_key: str) -> VikingClient:
        if not self._client:
            client = await VikingClient.create()
            self._client = client
        return self._client

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_id
        logger.info(f"OpenVikingCompactHook: message={vikingbot_session.messages}")
        try:
            client = await self._get_client(session_id)
            result = await client.commit(session_id, vikingbot_session.messages)
            return result
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    def __init__(self):
        self._client = None

    async def _get_client(self, session_key: str) -> ov.AsyncOpenViking:
        if not self._client:
            ov_data_path = get_data_dir() / "ov_data"
            ov_data_path.mkdir(parents=True, exist_ok=True)
            client = ov.AsyncOpenViking(path=str(ov_data_path))
            await client.initialize()
            self._client = client
        return self._client

    async def execute(self, context: HookContext,  tool_name, params, result) -> Any:
        if tool_name == 'read_file':
            """result is like following, the skill name is weather:
                ---
                name: weather
                description: Get current weather and forecasts (no API key required).
            """
            if result:  
                if not isinstance(result, Exception):
                    result = f'hahahahahaha:\n{result}'
        return {
            'tool_name': tool_name,
            'params': params,
            'result': result
        }


hooks = {
    'message.compact':[
        OpenVikingCompactHook()
    ],
    'tool.post_call':[
        OpenVikingPostCallHook()
    ]
}