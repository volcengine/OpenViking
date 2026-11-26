# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Check the installed Tau Gym API used by both VikingBot runners."""

import inspect
import sys
from pathlib import Path


def main() -> None:
    from tau2.gym.gym_agent import AgentGymEnv

    try:
        inspect.signature(AgentGymEnv).bind_partial(defer_user_stop=True)
        inspect.signature(AgentGymEnv.close).bind_partial(termination_reason="agent_stop")
    except (AttributeError, TypeError) as exc:
        raise SystemExit(
            "This Tau Gym lacks deferred STOP support. Install the pinned checkout "
            "with benchmark/tau2/vikingbot/setup_env.sh."
        ) from exc

    if len(sys.argv) > 1:
        # setup_env.sh uses an editable install. Its data and code must come
        # from the same checkout, including when reusing an existing venv.
        gym_source = Path(inspect.getfile(AgentGymEnv)).resolve()
        if not gym_source.is_relative_to(Path(sys.argv[1]).resolve()):
            raise SystemExit(f"The installed Tau Gym does not come from {sys.argv[1]}.")


if __name__ == "__main__":
    main()
