// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// What an `agy acp` initialize answer must show before Vika drives it: the
// adapter delivers the conversation policy through the appended system prompt,
// so an agent without it would run with no policy at all. Shared with setup's
// probe, which must not load the daemon adapter. Returns '' when acceptable.
export function agyHandshakeProblem(result) {
  if (!/antigravity/i.test(result?.agentInfo?.name ?? '')) return 'unrecognised ACP agent';
  if (result?.agentCapabilities?._meta?.antigravity?.systemPrompt?.append !== true) {
    return 'this agy cannot append a system prompt; update Antigravity';
  }
  return '';
}
