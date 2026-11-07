// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Match the server's slash normalization before any policy check. Refuse
// ambiguous encodings and traversal rather than interpreting them differently
// from the server. Return the exact URI that is safe to send after checking.
export function normalizeOVUri(uri) {
  const value = String(uri ?? '');
  const parts = value.slice('viking://'.length).split('/').filter(Boolean);
  if (!value.startsWith('viking://') || /[\s%\\?\u0000-\u001f\u007f]/.test(value)
    || parts.some((part) => part === '.' || part === '..')) {
    throw Object.assign(new Error('Invalid or ambiguous OpenViking URI'), { code: 'invalid_input' });
  }
  return `viking://${parts.join('/')}`;
}

export function privateMailUri(uri) {
  const value = normalizeOVUri(uri);
  return /^viking:\/\/(?:~|user\/[^/]+)\/resources\/emails(?:\/|$)/.test(value);
}

// Ancestor summaries may contain abstracts of the private mail subtree.
export function mailAncestorSummary(uri) {
  return /^viking:\/\/(?:(?:~|user(?:\/[^/]+)?)(?:\/resources)?\/)?\.(?:abstract|overview)\.md$/.test(normalizeOVUri(uri));
}

export function mailAncestorUri(uri) {
  return /^viking:\/\/(?:~|user(?:\/[^/]+)?|(?:~|user\/[^/]+)\/resources)?$/.test(normalizeOVUri(uri));
}

export function privateMailFilter(user) {
  if (!user || /[\s/%\\?]/.test(user)) {
    throw Object.assign(new Error('OpenViking did not identify its authenticated user'), { code: 'openviking_unavailable' });
  }
  const ancestors = ['viking://', 'viking://user', `viking://user/${user}`, `viking://user/${user}/resources`];
  return { op: 'and', conds: [
    { op: 'must_not', field: 'uri', conds: [`viking://user/${user}/resources/emails`], para: '-d=-1' },
    { op: 'must_not', field: 'uri', conds: ancestors.flatMap((uri) => {
      const prefix = uri === 'viking://' ? uri : `${uri}/`;
      return [uri, `${prefix}.abstract.md`, `${prefix}.overview.md`];
    }), para: '-d=0' },
  ] };
}

export function visibleWithoutMail(uri) {
  try { return !privateMailUri(uri) && !mailAncestorSummary(uri); } catch { return false; }
}

export function assertMailAccess(uri, access) {
  const normalized = normalizeOVUri(uri);
  if (!access?.allowPrivateMail && !visibleWithoutMail(normalized)) {
    throw Object.assign(new Error('Private mail requires an owner request; use its decision_id only when serving that request'),
      { code: 'private_mail_denied' });
  }
  return normalized;
}
