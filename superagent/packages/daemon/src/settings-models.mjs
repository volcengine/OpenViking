// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Discover options without starting a router conversation or a worker task.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { getRuntimeDefinition, modelIdPattern, staticModelEntries } from './runtime-catalog.mjs';
import { loadDurableModels, availableChatModels } from './durable-models.mjs';

const execute = promisify(execFile);
const cache = new Map();
export function cachedFileModels(runtime, directory) {
  const { models = [] } = JSON.parse(fs.readFileSync(path.join(directory, 'models_cache.json'), 'utf8'));
  return models.filter((m) => !m.hidden && (!m.visibility || m.visibility === 'list')).map((m) => ({
    id: m.slug || m.id,
    defaultEffort: m.default_reasoning_level,
    efforts: (m.supported_reasoning_levels ?? []).map((e) => e.effort).filter(Boolean),
  })).filter((m) => modelIdPattern(runtime).test(m.id ?? ''));
}

async function discover(runtime, config) {
  if (runtime === 'codex') return cachedFileModels(runtime, process.env.CODEX_HOME || path.join(os.homedir(), '.codex'));
  if (runtime === 'traex') return cachedFileModels(runtime, path.join(process.env.TRAE_HOME || path.join(os.homedir(), '.trae'), 'cli'));
  if (runtime === 'pi' || runtime === 'durable') {
    const { models } = await loadDurableModels();
    return (await availableChatModels(models)).map((m) => ({
      id: `${m.provider}/${m.id}`, efforts: m.reasoning ? getRuntimeDefinition(runtime).efforts : ['off'],
    }));
  }
  if (runtime === 'opencode' || runtime === 'agy') {
    const bin = runtime === 'opencode' ? config.opencodeBin || 'opencode' : config.agyBin || 'agy';
    const { stdout } = await execute(bin, runtime === 'opencode' ? ['models', '--pure'] : ['models'], {
      timeout: 8000, maxBuffer: 2 * 1024 * 1024, cwd: os.homedir(), windowsHide: true,
    });
    return stdout.split(/\r?\n/).map((line) => line.trim().split(/\t/)[0])
      .filter((id) => modelIdPattern(runtime).test(id) && (runtime !== 'opencode' || id.includes('/')))
      .map((id) => ({ id, efforts: [] }));
  }
  return staticModelEntries(runtime);
}

export async function settingsModels(snapshot, runtimes, config = {}) {
  const catalogs = {};
  await Promise.all([...new Set(runtimes)].filter(Boolean).map(async (runtime) => {
    if (runtime === snapshot.runtime && snapshot.models?.length) {
      catalogs[runtime] = snapshot.models;
      return;
    }
    const key = `${runtime}:${config.opencodeBin || ''}:${config.agyBin || ''}`;
    const previous = cache.get(key);
    if (previous && Date.now() - previous.at < 30000) { catalogs[runtime] = previous.models; return; }
    try {
      const models = await discover(runtime, config);
      const unique = [...new Map(models.filter((m) => modelIdPattern(runtime).test(m.id ?? '')).map((m) => [m.id, m])).values()];
      catalogs[runtime] = unique;
      cache.set(key, { at: Date.now(), models: unique });
    } catch {
      catalogs[runtime] = previous?.models ?? staticModelEntries(runtime);
    }
  }));
  return { ...snapshot, catalogs };
}
