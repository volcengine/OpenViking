// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  defaultDshSkillRoots, dshAgentsHomePath, dshHomeMode, dshHomePath, dshHostHome,
  dshSkillDirectories, dshSkillRootChoices,
} from '../src/dsh-config.mjs';

function temp(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-config-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('isolated mode offers six roots in setup order and shared mode offers four', () => {
  assert.deepEqual(dshSkillRootChoices(), [
    'workspace-dsh', 'project-agents', 'host-agents', 'host-dsh', 'data-dsh', 'data-agents',
  ]);
  assert.deepEqual(defaultDshSkillRoots(), [
    'workspace-dsh', 'project-agents', 'data-dsh', 'data-agents',
  ], 'isolated mode leaves both host roots unchecked by default');
  assert.deepEqual(defaultDshSkillRoots('shared'), [
    'workspace-dsh', 'project-agents', 'host-agents', 'host-dsh',
  ]);
  assert.equal(dshHomeMode('anything-else'), 'isolated');
});

test('isolated and shared homes resolve to the instance and host respectively', t => {
  const root = temp(t);
  const dataDir = path.join(root, 'data');
  const homeDir = path.join(root, 'home');
  assert.equal(dshHomePath({ dataDir, homeDir }), path.join(dataDir, 'dsh', 'home'));
  assert.equal(dshAgentsHomePath({ dataDir, homeDir }), path.join(dataDir, 'dsh', 'agents'));
  assert.equal(dshHomePath({ dataDir, mode: 'shared', homeDir }), path.join(homeDir, '.dsh'));
  assert.equal(dshAgentsHomePath({ dataDir, mode: 'shared', homeDir }), path.join(homeDir, '.agents'));
  assert.equal(dshHostHome({ USERPROFILE: homeDir }), homeDir);
  assert.throws(() => dshHomePath({}), /dataDir/);
});

test('only checked roots resolve, with projectRoot based on the nearest .git ancestor', t => {
  const root = temp(t);
  const projectRoot = path.join(root, 'project');
  const workspace = path.join(projectRoot, 'nested', 'workspace');
  fs.mkdirSync(path.join(projectRoot, '.git'), { recursive: true });
  const dataDir = path.join(root, 'data');
  const homeDir = path.join(root, 'home');
  assert.deepEqual(dshSkillDirectories({ workspace, dataDir, homeDir }), [
    path.join(workspace, '.dsh', 'skills'),
    path.join(projectRoot, '.agents', 'skills'),
    path.join(dataDir, 'dsh', 'home', 'skills'),
    path.join(dataDir, 'dsh', 'agents', 'skills'),
  ], 'default resolution also leaves the host roots out');
  assert.deepEqual(dshSkillDirectories({
    workspace, dataDir, homeDir, selected: ['host-dsh', 'workspace-dsh'],
  }), [
    path.join(workspace, '.dsh', 'skills'),
    path.join(homeDir, '.dsh', 'skills'),
  ]);
  assert.deepEqual(dshSkillDirectories({ workspace, dataDir, homeDir, selected: [] }), []);
});

test('shared mode resolves only its four roots and ignores isolated-only ids', t => {
  const root = temp(t);
  const workspace = path.join(root, 'workspace');
  const dataDir = path.join(root, 'data');
  const homeDir = path.join(root, 'home');
  assert.deepEqual(dshSkillDirectories({ workspace, dataDir, homeDir, mode: 'shared' }), [
    path.join(workspace, '.dsh', 'skills'),
    path.join(workspace, '.agents', 'skills'),
    path.join(homeDir, '.agents', 'skills'),
    path.join(homeDir, '.dsh', 'skills'),
  ]);
  assert.deepEqual(dshSkillDirectories({
    workspace, dataDir, homeDir, mode: 'shared', selected: ['data-dsh', 'data-agents'],
  }), []);
});
