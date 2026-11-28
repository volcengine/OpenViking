// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// lark-cli refuses to work while it believes it runs inside another agent's
// workspace: `config init` exits 3 with "config init is refused inside <agent>
// context (would create a parallel app and shadow the existing <agent>
// binding)", and `auth status` fails with "<agent> context detected but
// lark-cli is not bound to it". Setup then can neither create the bot profile
// nor read the owner, so an OpenViking SuperAgent host that also runs one of
// those agents can never connect Feishu.
//
// The marker is the agent's home variable, and any non-empty value triggers the
// refusal - a relative or nonexistent path is enough, while an empty value is
// not. SuperAgent keeps its own lark-cli profile, so a host agent's workspace
// must not leak into the child environment.
export const AGENT_WORKSPACE_ENV = Object.freeze(['HERMES_HOME', 'OPENCLAW_HOME']);

const WORKSPACE_KEYS = new Set(AGENT_WORKSPACE_ENV);

// Windows environment names are case-insensitive; compare upper-cased so a
// `hermes_home` inherited from a lower-casing shell is stripped too.
export function isAgentWorkspaceEnvKey(name) {
  return typeof name === 'string' && WORKSPACE_KEYS.has(name.toUpperCase());
}
