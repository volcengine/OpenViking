"""存储后端可选执行容量接缝；不参与鉴权和业务handler。"""
from openviking.server.dependencies import get_service

class StateCapacityMiddleware:
    def __init__(self,app):
        self.app=app
        self.bound=None
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and self.bound is None:
            try:
                service=get_service()
            except RuntimeError:
                service=None
            factory=getattr(getattr(service,'_agfs_client',None),'http_capacity_middleware',None)
            if factory:
                cls,params=factory()
                self.bound=cls(self.app,**params)
        await (self.bound or self.app)(scope,receive,send)
