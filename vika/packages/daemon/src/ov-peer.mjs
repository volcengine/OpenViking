// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

// OpenViking keeps memories per user and, within a user, per peer: someone the
// user deals with. The instance is the user, so its owner speaks as that user
// and carries no peer. Everyone else the agent talks to, people and other bots
// alike, is a peer named after the identity their channel gave them. That
// identity is stable within one instance (one Feishu app, one Slack workspace,
// one mailbox), and the instance is the user the peer belongs to. The same
// person on two channels is two peers.
//
// A mail sender is named by the From address, which no one has verified: a
// forged address writes under that address's own peer and nobody else's. A
// mailbox reports no owner, so a mail is never the owner's own words.

// OV peer ids: one path segment of letters, digits, `_ . @ -`, at most one `@`.
const DISALLOWED = /[^A-Za-z0-9_.@-]/g;
const MAX_PEER_ID = 255;

// The peer an event's sender is, or null when it has no sender.
export function peerForEvent(event) {
  const id = String(event?.senderId ?? '').trim();
  if (!id) return null;
  const source = String(event.source || 'unknown');
  if (source === 'email' || event.senderType === 'email') return peerId(`email-${id.replace(/^email:/, '').toLowerCase()}`);
  // A Slack user id is unique within its workspace only.
  if (source === 'slack' && event.tenantKey) return peerId(`slack-${event.tenantKey}-${id}`);
  return peerId(`${source}-${id}`);
}

// A name OV accepts. One that had to change, or be cut, gets a digest of the
// original so two identities cannot end up as one peer.
function peerId(raw) {
  let clean = raw.replace(DISALLOWED, '_');
  const at = clean.indexOf('@');
  if (at >= 0) clean = `${clean.slice(0, at + 1)}${clean.slice(at + 1).replaceAll('@', '_')}`;
  if (clean === raw && clean.length <= MAX_PEER_ID) return clean;
  const digest = createHash('sha256').update(raw).digest('hex').slice(0, 12);
  return `${clean.slice(0, MAX_PEER_ID - digest.length - 1)}-${digest}`;
}
