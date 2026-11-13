from typing import Any


def session_budget(agents: Any) -> tuple[int, int, int]:
    window = int(getattr(agents, "context_window_tokens", 128000) or 128000)
    read_budget = int(getattr(agents, "effective_session_context_token_budget", window))
    trigger = getattr(agents, "effective_commit_token_threshold", None)
    if trigger is None:
        trigger = max(1, int(window * float(getattr(agents, "async_compress_ratio", 0.7))))
    return window, read_budget, trigger
