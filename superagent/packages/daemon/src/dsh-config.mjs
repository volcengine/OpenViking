// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH's two configuration layouts and the skill roots setup lets the owner
// enable. Manifest values store logical root ids, not absolute paths: changing
// the workspace in setup must move workspace/project roots with it.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const DSH_HOME_ISOLATED = 'isolated';
export const DSH_HOME_SHARED = 'shared';
export const DSH_HOME_MODES = Object.freeze([DSH_HOME_ISOLATED, DSH_HOME_SHARED]);

export function dshHomeMode(value) {
  return value === DSH_HOME_SHARED ? DSH_HOME_SHARED : DSH_HOME_ISOLATED;
}

export function dshHostHome(environment = process.env, fallback = os.homedir()) {
  return environment?.HOME || environment?.USERPROFILE || fallback;
}

export function dshHomePath({ dataDir, mode = DSH_HOME_ISOLATED, homeDir = os.homedir() } = {}) {
  if (dshHomeMode(mode) === DSH_HOME_SHARED) return path.join(homeDir, '.dsh');
  if (typeof dataDir !== 'string' || !dataDir) throw new Error('dsh dataDir is required');
  return path.join(dataDir, 'dsh', 'home');
}

export function dshAgentsHomePath({ dataDir, mode = DSH_HOME_ISOLATED, homeDir = os.homedir() } = {}) {
  if (dshHomeMode(mode) === DSH_HOME_SHARED) return path.join(homeDir, '.agents');
  if (typeof dataDir !== 'string' || !dataDir) throw new Error('dsh dataDir is required');
  return path.join(dataDir, 'dsh', 'agents');
}

export const DSH_SKILL_ROOTS = Object.freeze([
  Object.freeze({ id: 'workspace-dsh', modes: DSH_HOME_MODES }),
  Object.freeze({ id: 'project-agents', modes: DSH_HOME_MODES }),
  Object.freeze({ id: 'host-agents', modes: DSH_HOME_MODES }),
  Object.freeze({ id: 'host-dsh', modes: DSH_HOME_MODES }),
  Object.freeze({ id: 'data-dsh', modes: Object.freeze([DSH_HOME_ISOLATED]) }),
  Object.freeze({ id: 'data-agents', modes: Object.freeze([DSH_HOME_ISOLATED]) }),
]);

export function dshSkillRootChoices(mode = DSH_HOME_ISOLATED) {
  const normalized = dshHomeMode(mode);
  return DSH_SKILL_ROOTS.filter(root => root.modes.includes(normalized)).map(root => root.id);
}

export function defaultDshSkillRoots(mode = DSH_HOME_ISOLATED) {
  const normalized = dshHomeMode(mode);
  return dshSkillRootChoices(normalized).filter(id => normalized === DSH_HOME_SHARED || !id.startsWith('host-'));
}

// The nearest .git ancestor is DSH's project root. A .git file is accepted too
// (worktrees and submodules); without either, the workspace itself is the root.
export function dshProjectRoot(workspace) {
  const resolved = path.resolve(workspace);
  let current = resolved;
  for (;;) {
    if (fs.existsSync(path.join(current, '.git'))) return current;
    const parent = path.dirname(current);
    if (parent === current) return resolved;
    current = parent;
  }
}

export function dshSkillDirectories({
  workspace, dataDir, mode = DSH_HOME_ISOLATED, selected,
  homeDir = os.homedir(),
} = {}) {
  if (typeof workspace !== 'string' || !workspace) throw new Error('dsh workspace is required');
  const choices = dshSkillRootChoices(mode);
  const enabled = new Set(selected === undefined ? defaultDshSkillRoots(mode) : selected.filter(id => choices.includes(id)));
  const projectRoot = dshProjectRoot(workspace);
  const directories = {
    'workspace-dsh': path.join(workspace, '.dsh', 'skills'),
    'project-agents': path.join(projectRoot, '.agents', 'skills'),
    'host-agents': path.join(homeDir, '.agents', 'skills'),
    'host-dsh': path.join(homeDir, '.dsh', 'skills'),
    'data-dsh': path.join(dshHomePath({ dataDir, mode, homeDir }), 'skills'),
    'data-agents': path.join(dshAgentsHomePath({ dataDir, mode, homeDir }), 'skills'),
  };
  return choices.filter(id => enabled.has(id)).map(id => directories[id]);
}
