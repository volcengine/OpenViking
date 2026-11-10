// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import os from 'node:os';
import { homeDir as platformHomeDir } from '../../daemon/src/platform/index.mjs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  CONTROL_ENV_MAX_BYTES,
  loadDeploymentManifest,
  updaterTargetsFor,
} = require('../../../deployment-config.cjs');

function parseUpdaterTargets(raw) {
  if (Buffer.byteLength(raw) > CONTROL_ENV_MAX_BYTES) {
    throw new Error(`VIKA_UPDATER_TARGETS_JSON exceeds ${CONTROL_ENV_MAX_BYTES} bytes`);
  }
  let value;
  try { value = JSON.parse(raw); } catch {
    throw new Error('VIKA_UPDATER_TARGETS_JSON must be valid JSON');
  }
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length === 0) {
    throw new Error('VIKA_UPDATER_TARGETS_JSON must be a non-empty object');
  }
  return Object.fromEntries(Object.entries(value).map(([id, spec]) => {
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(id) || ['all', 'web', 'updater'].includes(id)) {
      throw new Error(`invalid updater target id: ${id}`);
    }
    const expectedApp = `vika-${id}`;
    if (spec?.app !== expectedApp) throw new Error(`${id}.app must be ${expectedApp}`);
    if (spec?.readyUrl !== `http://127.0.0.1:${new URL(spec?.readyUrl).port}/ready`
      || !/^http:\/\/127\.0\.0\.1:\d+\/ready$/.test(spec.readyUrl)) {
      throw new Error(`${id}.readyUrl must be a loopback ready endpoint`);
    }
    if (typeof spec.upgradeSharedWeb !== 'boolean') {
      throw new Error(`${id}.upgradeSharedWeb must be a boolean`);
    }
    return [id, Object.freeze({
      app: expectedApp,
      config: 'ecosystem.config.cjs',
      readyUrl: spec.readyUrl,
      upgradeSharedWeb: spec.upgradeSharedWeb,
    })];
  }));
}

// Read on every call: the manifest can gain an instance while the updater
// runs. VIKA_UPDATER_TARGETS_JSON replaces the manifest, for tests.
export function loadTargets(env = process.env) {
  const raw = env.VIKA_UPDATER_TARGETS_JSON
    || JSON.stringify(updaterTargetsFor(loadDeploymentManifest(env)));
  return Object.freeze(parseUpdaterTargets(raw));
}

export const SHARED_WEB = Object.freeze({
  app: 'vika-web',
  config: 'ecosystem.config.cjs',
  readyUrl: 'http://127.0.0.1:3737/ready',
});

export function upgradeTargetNames(targets = loadTargets()) {
  return [...Object.keys(targets), 'all'];
}

export function getTarget(target, targets = loadTargets()) {
  if (typeof target !== 'string' || !Object.hasOwn(targets, target)) {
    throw new Error(`target must be one of: ${upgradeTargetNames(targets).join(', ')}`);
  }
  return targets[target];
}

export function getUpgradeTargets(target, targets = loadTargets()) {
  if (target === 'all') return Object.entries(targets);
  return [[target, getTarget(target, targets)]];
}

export function assertUpgradeTarget(target, targets = loadTargets()) {
  if (typeof target !== 'string' || !upgradeTargetNames(targets).includes(target)) {
    throw new Error(`target must be one of: ${upgradeTargetNames(targets).join(', ')}`);
  }
  return target;
}

export function shouldUpgradeSharedWeb(target, targets = loadTargets()) {
  return target === 'all' || getTarget(target, targets).upgradeSharedWeb === true;
}

export function defaultSocketPath(homeDir = platformHomeDir() || os.homedir()) {
  return path.join(homeDir, '.ovsa', 'updater.sock');
}

export function defaultReleaseLink(homeDir = platformHomeDir() || os.homedir()) {
  return path.join(homeDir, '.ovsa', 'current');
}
