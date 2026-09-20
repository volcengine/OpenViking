"""Account for provider invocations independently of agent-loop iterations."""

from vikingbot.providers.base import LLMProvider


class BudgetedProvider(LLMProvider):
    def __init__(self, provider, turn):
        super().__init__(api_key=provider.api_key, api_base=provider.api_base)
        self.provider = provider
        self.turn = turn
        self.calls = 0

    def _begin(self, kind):
        store = self.turn.service.store
        store.assert_authorized(self.turn.task_id)
        if self.calls >= self.turn.service.config.longtask.model_calls_per_round:
            raise RuntimeError("Long-task model invocation budget reached")
        self.calls += 1
        return store.begin_operation(
            self.turn.task_id,
            self.turn.turn_id,
            "model_request",
            {"kind": kind, "call": self.calls},
        )

    async def chat(self, **kwargs):
        operation = self._begin("chat")
        response = await self.provider.chat(**kwargs)
        self.turn.service.store.end_operation(operation, {"usage": response.usage})
        return response

    async def chat_stream(self, **kwargs):
        operation = self._begin("chat_stream")
        usage = None
        async for event in self.provider.chat_stream(**kwargs):
            if event.type == "response" and event.response is not None:
                usage = event.response.usage
            yield event
        self.turn.service.store.end_operation(operation, {"usage": usage})

    def supports_tool_result_media(self, model=None):
        return self.provider.supports_tool_result_media(model)

    def get_default_model(self):
        return self.provider.get_default_model()
