// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// dsh needs Node >= 24.9 (assertDshNodeVersion), while OpenViking SuperAgent itself supports
// older Node 22 releases. Tests that start a real or fake dsh session are skipped
// on those, instead of failing the whole suite; CI on Node 24 still runs them.
import nodeTest, { describe as nodeDescribe, it as nodeIt } from 'node:test';
import { assertDshNodeVersion } from '../src/dsh-runtime.mjs';

let supported = true;
try { assertDshNodeVersion(); } catch { supported = false; }
export const dshNodeSupported = supported;
const reason = `dsh requires Node >= 24.9 (running ${process.versions.node})`;

function gate(run) {
  if (supported) return run;
  return (name, options, fn) => (typeof options === 'function' || options === undefined
    ? run(name, { skip: reason }, options)
    : run(name, { ...options, skip: reason }, fn));
}

export const test = gate(nodeTest);
export const describe = gate(nodeDescribe);
export const it = gate(nodeIt);
