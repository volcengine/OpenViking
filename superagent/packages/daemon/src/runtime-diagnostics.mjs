// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { stripVTControlCharacters } from 'node:util';
import { redactSensitive } from './logger.mjs';

// Keep the beginning, not a tail that could lose the key before a secret.
// Redact after assembling chunks so a token split across writes stays hidden.
// Continue draining stderr after stop(), but retain no runtime conversation.
export function captureStartupStderr(stream) {
  let captured = '';
  let active = true;
  stream?.setEncoding('utf8');
  stream?.on('data', (chunk) => {
    if (active) captured += String(chunk).slice(0, Math.max(0, 8192 - captured.length));
  });
  return {
    text: () => redactSensitive(stripVTControlCharacters(captured))
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, 2000),
    stop() { active = false; captured = ''; },
  };
}
