import asyncio
import importlib
from collections import defaultdict
from typing import List, Any, Dict, Type

from loguru import logger

from .base import Hook, HookContext

try:
    from vikingbot.hooks.builtins.openviking_hooks import hooks as _openviking_hooks
except Exception as e:
    logger.warning(f"OpenViking built-in hooks unavailable: {e}")
    _openviking_hooks = {}


class HookManager:
    def __init__(self):
        self._hooks: Dict[str, List[Type[Hook]]] = defaultdict(list)

    def import_path(self, path):
        module_path, attr_name = path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            logger.exception(f"模块 {module_path} 导入失败：{e}")
            return None
        try:
            # 核心：获取模块内的 hooks 属性
            hooks_attr = getattr(module, attr_name)
            return hooks_attr
        except AttributeError as e:
            logger.exception(f"模块 {module_path} 中不存在 {attr_name} 属性：{e}")
            return None

    def register_path(self, hook_path_list) -> None:
        for hook_path in hook_path_list or []:
            hooks = self.import_path(hook_path)
            if not hooks:
                continue
            for event_type, hook_types in hooks.items():
                for hook_type in hook_types:
                    self._hooks[event_type].append(hook_type)
                    logger.debug(f"Registered hook '{hook_type}' for event '{event_type}'")

    async def execute_hooks(self, context: HookContext, **kwargs) -> List[Any]:
        async_hooks = [hook for hook in self._hooks[context.event_type] if not hook.is_sync]
        sync_hooks = [hook for hook in self._hooks[context.event_type] if hook.is_sync]
        if async_hooks:
            logger.debug(
                f"Executing {len(async_hooks)} async hooks for event '{context.event_type}'"
            )
            async_results = await asyncio.gather(
                *[hook.execute(context, **kwargs) for hook in async_hooks], return_exceptions=True
            )
            for i, result in enumerate(async_results):
                if isinstance(result, Exception):
                    logger.error(f"Hook '{async_hooks[i].name}' failed: {result}")

        if sync_hooks:
            logger.debug(f"Executing {len(sync_hooks)} sync hooks for event '{context.event_type}'")
            for hook in sync_hooks:
                kwargs = await hook.execute(context, **kwargs)
        return kwargs


hook_manager = HookManager()
