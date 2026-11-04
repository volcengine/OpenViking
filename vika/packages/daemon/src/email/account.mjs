// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { safeError } from '../logger.mjs';
import { ImapConnection } from './imap.mjs';
import { refreshAccessToken, saveRefreshToken, tokenFresh } from './oauth2.mjs';
import { verifySmtp } from './smtp.mjs';

// The mailbox the Connector reads. One mailbox per instance.
export const MAILBOX = 'INBOX';

// What this Connector speaks. The manifest and the check it answers both name
// it, so it is written once: a mismatch would say the running worker is not the
// one the manifest describes.
export const EMAIL_VERSION = 'imap-smtp-v2';

// The account file: { address, imap: { host, port }, smtp: { host, port,
// secure? }, username?, name? } plus either `password`, a client (app)
// password, or `oauth2: { tenant, clientId, refreshToken }` for a mailbox whose
// provider turned password sign-in off (Microsoft 365, Outlook.com). SMTP uses
// implicit TLS on port 465 unless `secure` says otherwise, else STARTTLS.
//
// `username` is what the protocols sign in with, and it is not always the
// address: a Microsoft account reached through an alias has one primary address,
// and Exchange Online accepts the alias for IMAP but not for SMTP AUTH. An
// owner whose mailbox is such an alias must put the primary address here.
export function loadAccount(file) {
  let value;
  try { value = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {
    throw new Error('email config file is unreadable or not JSON');
  }
  const address = String(value?.address ?? '').trim().toLowerCase();
  if (!/^[^\s@<>]+@[^\s@<>]+$/.test(address)) throw new Error('email config needs an address');
  const oauth2 = value?.oauth2 && typeof value.oauth2 === 'object' ? accountOAuth2(value.oauth2) : null;
  if (!oauth2 && (typeof value.password !== 'string' || !value.password)) {
    throw new Error('email config needs a password or oauth2');
  }
  // A file carried over from a password account and given an `oauth2` block is
  // easy to get wrong by leaving the old `password` in place. Saying so beats
  // quietly using one of them: the unused credential is a sign the file is not
  // what its owner thinks it is.
  if (oauth2 && typeof value.password === 'string' && value.password) {
    throw new Error('email config has both a password and oauth2; keep only the one this mailbox signs in with');
  }
  const server = (key) => {
    const host = String(value[key]?.host ?? '').trim();
    const port = Number(value[key]?.port);
    if (!host || !Number.isInteger(port) || port < 1 || port > 65_535) throw new Error(`email config needs ${key}.host and ${key}.port`);
    return { host, port, ...(typeof value[key].secure === 'boolean' ? { secure: value[key].secure } : {}) };
  };
  return Object.freeze({
    address,
    name: typeof value.name === 'string' ? value.name.trim() : '',
    user: typeof value.username === 'string' && value.username.trim() ? value.username.trim() : address,
    ...(oauth2 ? { oauth2 } : { password: value.password }),
    imap: server('imap'),
    smtp: server('smtp'),
    // The refresh token rotates; `oauth2` stays mutable so the new one can be
    // written back without reloading the file.
    file,
  });
}

// The two identifiers are the owner's own Entra application; the refresh token
// is the credential the sign-in left behind.
function accountOAuth2(value) {
  const tenant = String(value.tenant || 'common').trim();
  const clientId = String(value.clientId ?? '').trim();
  const refreshToken = String(value.refreshToken ?? '').trim();
  if (!/^[A-Za-z0-9.-]+$/.test(tenant)) throw new Error('email config oauth2.tenant must be a tenant id or domain');
  if (!clientId) throw new Error('email config needs oauth2.clientId');
  if (!refreshToken) throw new Error('email config needs oauth2.refreshToken');
  return { tenant, clientId, refreshToken };
}

// What a connection signs in with: the saved password, or an access token
// refreshed from the saved refresh token and reused until it is about to
// expire. A rotated refresh token is written back to the account file; failing
// to write it is not fatal, the token in memory still works.
//
// One refresh at a time. An IMAP connect and an SMTP send can ask at the same
// moment, and the refresh token rotates: two refreshes racing on the same one
// leave the second failing, or the slower response writing an older token back
// over the newer. A caller that arrives during a refresh waits for it instead.
//
// `stale` asks for a refresh even though the cached token looks fresh: a server
// that just refused it is better evidence than our own expiry clock.
export function credentialSource(account, { fetch: fetchImpl = globalThis.fetch, now = Date.now, logger } = {}) {
  let tokens = null;
  let inFlight = null;
  return async ({ stale = false } = {}) => {
    if (!account.oauth2) return { password: account.password };
    if (!stale && tokenFresh(tokens, now())) return { accessToken: tokens.accessToken };
    if (!inFlight) {
      const usedRefreshToken = account.oauth2.refreshToken;
      inFlight = (async () => {
        const refreshed = await refreshAccessToken(account.oauth2, { fetch: fetchImpl, now });
        tokens = refreshed;
        if (refreshed.refreshToken !== account.oauth2.refreshToken) {
          account.oauth2.refreshToken = refreshed.refreshToken;
          try {
            // Only when the file still holds the token this refresh spent: a
            // sign-in done meanwhile has a newer one, and that one is kept.
            saveRefreshToken(account.file, account.oauth2, { onlyIfRefreshedFrom: usedRefreshToken });
          } catch (error) {
            logger?.warn('email_token_not_saved', { error: safeError(error) });
          }
        }
        return refreshed;
      })().finally(() => { inFlight = null; });
    }
    // A failed refresh is not cached: the next caller tries again.
    return { accessToken: (await inFlight).accessToken };
  };
}

// Ids carry which account a mail came from: UIDs and UIDVALIDITY are only
// unique within one mailbox, and an instance may switch to another account.
export function accountTag(address) {
  return createHash('sha256').update(address).digest('hex').slice(0, 8);
}

// Both credentials: an IMAP sign-in with a read-only look at the inbox, then
// SMTP AUTH. Sends nothing. Resolves with whether new mail is pushed (IDLE).
// An OAuth2 mailbox needs the caller's `credential` — the one just obtained, or
// the Connector's own.
export async function verifyAccount(account, { connectTls, credential } = {}) {
  const auth = credential ?? (account.oauth2 ? null : { password: account.password });
  if (!auth) throw new Error('OAuth2 mailbox needs an access token to check');
  const imap = new ImapConnection({ ...account.imap, ...(connectTls ? { connect: connectTls } : {}) });
  let idle;
  try {
    await imap.connect();
    if (auth.accessToken) await imap.authenticate(account.user, auth.accessToken);
    else await imap.login(account.user, auth.password);
    await imap.examine(MAILBOX);
    idle = imap.capabilities.has('IDLE');
    await imap.logout();
  } finally {
    imap.close();
  }
  await verifySmtp({ ...account.smtp, user: account.user, ...auth, ...(connectTls ? { connectTls } : {}) });
  return { idle };
}
