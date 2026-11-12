"""私有化可选容量接缝；无策略预算时直接使用原生 handler。"""

from openviking.server.dependencies import get_service


class StateCapacityMiddleware:
    def __init__(self, app):
        self.app = app
        self.bound = None
        self.resolved = False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not self.resolved:
            try:
                service = get_service()
            except RuntimeError:
                service = None
            factory = getattr(
                getattr(service, "_agfs_client", None), "http_capacity_middleware", None
            )
            if factory:
                result = factory()
                if result is not None:
                    cls, params = result
                    self.bound = cls(self.app, **params)
                self.resolved = True
        await (self.bound or self.app)(scope,receive,send)
