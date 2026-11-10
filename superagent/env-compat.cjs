// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
'use strict';

// Canonical keys win even when explicitly empty; OVSA aliases precede VIKA.
function normalizeEnv(source = {}) {
  const result = { ...source };
  for (const key of Object.keys(source)) {
    const match = /^(VIKA_|OVSA_)(.*)$/.exec(key);
    if (!match) continue;
    const canonical = `OV_SUPERAGENT_${match[2]}`;
    if (!Object.hasOwn(source, canonical)) {
      const intermediate = `OVSA_${match[2]}`;
      result[canonical] = Object.hasOwn(source, intermediate) ? source[intermediate] : source[key];
    }
    delete result[key];
  }
  return result;
}

// Entry points import this before modules that read process.env at load time.
const normalized = normalizeEnv(process.env);
for (const key of Object.keys(process.env)) {
  if (/^(VIKA_|OVSA_)/.test(key)) delete process.env[key];
}
Object.assign(process.env, normalized);
module.exports = { normalizeEnv };
