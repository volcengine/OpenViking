// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { GatewayConnection } from '@tencent-connect/qqbot-nodejs/protocol';

// Never pass SDK logs through: even debug dispatch logs contain message bodies
// and message_scene auth_token. Expose only our fixed diagnostics to Core.
export function createGateway({ api, signal, onMessage, onReady = () => {}, logger }) {
  return new GatewayConnection({
    account: { accountId: api.appId, appId: api.appId }, abortSignal: signal,
    intents: 1 << 25, reconnect: { maxAttempts: 3 }, handshakeTimeoutMs: 15_000,
    getAccessToken: () => api.accessToken(AbortSignal.any([signal, AbortSignal.timeout(15_000)])),
    clearTokenCache: () => api.clearToken(),
    getGatewayUrl: () => api.gateway(AbortSignal.any([signal, AbortSignal.timeout(15_000)])),
    onReady, onResumed: onReady, onMessage,
    onError: () => logger?.warn('qq_gateway_connection_failed'),
    onDisconnected: ({ code }) => logger?.warn('qq_gateway_disconnected', { code }),
  });
}

export function gatewayFailure(error) {
  const code = Number.isSafeInteger(error?.closeCode) ? ` (code ${error.closeCode})` : '';
  return new Error(`QQ gateway connection failed${code}; check network, bot status and C2C permission`);
}
