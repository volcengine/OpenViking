// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

// Display names are mutable, non-unique and supplied by other people. Memory
// attribution instead uses the connector's complete actor identity. The same
// token is used in OV journal from=, explicit remember and preference lookup.
// Identities on different connector instances are deliberately not merged.
export function personKey(event) {
  const id = event?.actor?.id ?? event?.senderId;
  if (!id) return null;
  const identity = [event.source ?? 'lark', event.connectorInstance ?? event.source ?? 'lark',
    event.tenantKey ?? '', event.actor?.type ?? event.senderType ?? 'user', id];
  return `person-${createHash('sha256').update(JSON.stringify(identity)).digest('hex').slice(0, 32)}`;
}

// Old display-name directories remain ordinary searchable memory. They are
// never inferred to be the owner's (or current sender's) standing preferences.
export const memoryPersonKey = (value) => /^person-[a-f0-9]{32}$/.test(String(value)) ? String(value) : null;
