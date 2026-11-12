// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { describe, it } from 'node:test';
import {
  DEFAULT_RUNTIME_ID,
  RUNTIME_ALIASES,
  RUNTIME_CATALOG,
  RUNTIME_IDS,
  getRuntimeDefinition,
  modelMatchesRuntime,
  parseRuntimeModelSelector,
  resolveRuntimeModelTarget,
  staticModelEntries,
} from '../src/runtime-catalog.mjs';

describe('runtime catalog', () => {
  it('owns runtime ids, aliases, static models, efforts, and capabilities', () => {
    assert.equal(DEFAULT_RUNTIME_ID, 'codex');
    assert.deepEqual(RUNTIME_IDS, ['codex', 'traex', 'claude', 'volc', 'pi', 'durable', 'dsh', 'opencode', 'agy']);
    assert.deepEqual(RUNTIME_ALIASES, {
      codex: 'codex',
      traex: 'traex',
      trae: 'traex',
      traecli: 'traex',
      'trae-cli': 'traex',
      claude: 'claude',
      volc: 'volc',
      volcengine: 'volc',
      ark: 'volc',
      pi: 'pi',
      durable: 'durable',
      'pi-durable': 'durable',
      pidurable: 'durable',
      dsh: 'dsh',
      'deepseek-harness': 'dsh',
      opencode: 'opencode',
      agy: 'agy',
      antigravity: 'agy',
    });
    assert.deepEqual(getRuntimeDefinition('claude').models, ['sonnet', 'opus', 'haiku', 'fable']);
    assert.deepEqual(getRuntimeDefinition('claude').efforts, ['low', 'medium', 'high', 'xhigh', 'max']);
    assert.equal(getRuntimeDefinition('codex').capabilities.sessionAuthority, 'preserve-process');
    assert.deepEqual(getRuntimeDefinition('traex').directAliases, ['traex', 'trae', 'traecli', 'trae-cli']);
    assert.deepEqual(getRuntimeDefinition('traex').efforts, [
      'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max',
    ]);
    assert.deepEqual(getRuntimeDefinition('traex').capabilities, {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    });
    assert.equal(getRuntimeDefinition('claude').capabilities.sessionAuthority, 'preserve-process');
    assert.deepEqual(getRuntimeDefinition('ark').models, ['ark']);
    assert.deepEqual(getRuntimeDefinition('volc').efforts, ['managed']);
    assert.deepEqual(getRuntimeDefinition('volc').capabilities, {
      modelCatalog: 'static',
      effortControl: 'remote-managed',
      activeInput: 'queue-next-turn',
      scopePolicy: 'single-conversation',
      outputOwnership: 'runtime',
      sessionAuthority: 'rotate',
      agentTools: 'lark-cli',
    });
    assert.deepEqual(getRuntimeDefinition('pi').directAliases, ['pi']);
    assert.deepEqual(getRuntimeDefinition('pi').efforts, [
      'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max',
    ]);
    assert.deepEqual(getRuntimeDefinition('pi').capabilities, {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    });
    assert.deepEqual(getRuntimeDefinition('durable').directAliases, ['durable', 'pi-durable', 'pidurable']);
    assert.deepEqual(getRuntimeDefinition('durable').efforts, [
      'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max',
    ]);
    assert.deepEqual(getRuntimeDefinition('durable').capabilities, {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    });
    assert.deepEqual(getRuntimeDefinition('opencode').directAliases, ['opencode']);
    assert.deepEqual(getRuntimeDefinition('opencode').efforts, []);
    assert.deepEqual(getRuntimeDefinition('opencode').capabilities, {
      modelCatalog: 'dynamic',
      effortControl: 'unsupported',
      activeInput: 'queue-next-turn',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    });
    assert.deepEqual(getRuntimeDefinition('agy').directAliases, ['agy', 'antigravity']);
    assert.deepEqual(getRuntimeDefinition('agy').models, []);
    assert.equal(getRuntimeDefinition('agy').ovIsolation, 'config-home');
    assert.deepEqual(getRuntimeDefinition('agy').capabilities, getRuntimeDefinition('opencode').capabilities);
    assert.deepEqual(RUNTIME_IDS.map((id) => [id, getRuntimeDefinition(id).capabilities.agentTools]), [
      ['codex', 'core'], ['traex', 'core'], ['claude', 'core'], ['volc', 'lark-cli'], ['pi', 'core'],
      ['durable', 'core'],
      ['dsh', 'core'],
      ['opencode', 'core'],
      ['agy', 'core'],
    ]);
    assert.equal(Object.isFrozen(RUNTIME_CATALOG), true);
    assert.equal(Object.isFrozen(getRuntimeDefinition('codex').capabilities), true);
    assert.equal(getRuntimeDefinition('__proto__'), null);
    const aliases = new Set();
    const models = new Set();
    for (const id of RUNTIME_IDS) {
      const definition = getRuntimeDefinition(id);
      for (const alias of definition.aliases) {
        assert.equal(aliases.has(alias), false, `duplicate alias: ${alias}`);
        aliases.add(alias);
      }
      for (const alias of definition.directAliases) {
        assert.equal(definition.aliases.includes(alias), true, `unknown direct alias: ${alias}`);
      }
      for (const model of definition.models) {
        assert.equal(models.has(model), false, `duplicate static model: ${model}`);
        models.add(model);
      }
      if (definition.defaultModel) {
        assert.equal(definition.models.includes(definition.defaultModel), true);
      }
    }
  });

  it('preserves direct, qualified, and compatibility target resolution', () => {
    const cases = [
      ['sonnet', { runtime: 'claude', model: 'sonnet', canonical: 'sonnet' }],
      ['gpt-5.6-sol', { runtime: 'codex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol' }],
      ['claude', { runtime: 'codex', model: 'claude', canonical: 'claude' }],
      ['codex', { runtime: 'codex', model: 'codex', canonical: 'codex' }],
      ['claude:sonnet', { runtime: 'claude', model: 'sonnet', canonical: 'sonnet' }],
      ['claude:', { runtime: 'claude', model: '', canonical: 'claude' }],
      ['codex:gpt-5.6-sol', { runtime: 'codex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol' }],
      ['codex:', { runtime: 'codex', model: '', canonical: '' }],
      ['traex', { runtime: 'traex', model: '', canonical: 'traex' }],
      ['traecli', { runtime: 'traex', model: '', canonical: 'traex' }],
      ['traex:gpt-5.6-sol', { runtime: 'traex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol' }],
      ['ark', { runtime: 'volc', model: 'ark', canonical: 'ark' }],
      ['volcengine', { runtime: 'volc', model: 'ark', canonical: 'ark' }],
      ['volc:', { runtime: 'volc', model: 'ark', canonical: 'ark' }],
      ['volcengine:custom', { runtime: 'volc', model: 'custom', canonical: 'custom' }],
      ['ark:model:variant', { runtime: 'volc', model: 'model:variant', canonical: 'model:variant' }],
      ['pi', { runtime: 'pi', model: '', canonical: 'pi' }],
      ['pi:', { runtime: 'pi', model: '', canonical: 'pi' }],
      ['pi:openai/gpt-4o', { runtime: 'pi', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o' }],
      ['durable', { runtime: 'durable', model: '', canonical: 'durable' }],
      ['pi-durable', { runtime: 'durable', model: '', canonical: 'durable' }],
      ['pidurable', { runtime: 'durable', model: '', canonical: 'durable' }],
      ['durable:', { runtime: 'durable', model: '', canonical: 'durable' }],
      ['durable:openai/gpt-4o', { runtime: 'durable', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o' }],
      ['opencode', { runtime: 'opencode', model: '', canonical: 'opencode' }],
      ['opencode:', { runtime: 'opencode', model: '', canonical: 'opencode' }],
      ['opencode:anthropic/claude-sonnet-5', {
        runtime: 'opencode', model: 'anthropic/claude-sonnet-5', canonical: 'anthropic/claude-sonnet-5',
      }],
      ['agy', { runtime: 'agy', model: '', canonical: 'agy' }],
      ['antigravity', { runtime: 'agy', model: '', canonical: 'agy' }],
      ['agy:', { runtime: 'agy', model: '', canonical: 'agy' }],
      ['agy:gemini-3.8-flash-low', { runtime: 'agy', model: 'gemini-3.8-flash-low', canonical: 'gemini-3.8-flash-low' }],
      ['foo:bar', { runtime: 'codex', model: 'foo:bar', canonical: 'foo:bar' }],
      ['Sonnet', { runtime: 'codex', model: 'Sonnet', canonical: 'Sonnet' }],
      ['ARK', { runtime: 'codex', model: 'ARK', canonical: 'ARK' }],
      ['constructor:x', { runtime: 'codex', model: 'constructor:x', canonical: 'constructor:x' }],
      ['toString:x', { runtime: 'codex', model: 'toString:x', canonical: 'toString:x' }],
      ['__proto__:x', { runtime: 'codex', model: '__proto__:x', canonical: '__proto__:x' }],
    ];
    for (const [value, expected] of cases) {
      assert.deepEqual(resolveRuntimeModelTarget(value), expected, value);
    }
  });

  it('parses command selectors without treating bare runtime-looking names as qualifiers', () => {
    assert.deepEqual(parseRuntimeModelSelector('volcengine'), {
      raw: 'volcengine',
      qualified: false,
      qualifier: null,
      runtime: null,
      model: 'ark',
    });
    assert.deepEqual(parseRuntimeModelSelector('claude:son'), {
      raw: 'claude:son',
      qualified: true,
      qualifier: 'claude',
      runtime: 'claude',
      model: 'son',
    });
    assert.deepEqual(parseRuntimeModelSelector('unknown:model'), {
      raw: 'unknown:model',
      qualified: false,
      qualifier: null,
      runtime: null,
      model: 'unknown:model',
    });
    assert.deepEqual(parseRuntimeModelSelector('pi'), {
      raw: 'pi',
      qualified: false,
      qualifier: null,
      runtime: null,
      model: '',
    });
    assert.deepEqual(parseRuntimeModelSelector('pi:openai/gpt-4o'), {
      raw: 'pi:openai/gpt-4o',
      qualified: true,
      qualifier: 'pi',
      runtime: 'pi',
      model: 'openai/gpt-4o',
    });
    assert.deepEqual(parseRuntimeModelSelector('trae:gpt-5.6-sol'), {
      raw: 'trae:gpt-5.6-sol',
      qualified: true,
      qualifier: 'trae',
      runtime: 'traex',
      model: 'gpt-5.6-sol',
    });
    assert.deepEqual(parseRuntimeModelSelector('traex'), {
      raw: 'traex',
      qualified: false,
      qualifier: null,
      runtime: null,
      model: '',
    });
  });

  it('filters live model ids using the same ownership rules as target resolution', () => {
    assert.equal(modelMatchesRuntime('sonnet', 'claude'), true);
    assert.equal(modelMatchesRuntime('ark', 'volcengine'), true);
    assert.equal(modelMatchesRuntime('gpt-5.6-sol', 'codex'), true);
    assert.equal(modelMatchesRuntime('openai/gpt-4o', 'pi'), true);
    assert.equal(modelMatchesRuntime('openai/gpt-4o', 'traex'), true);
    assert.equal(modelMatchesRuntime('sonnet', 'codex'), false);
    assert.equal(modelMatchesRuntime('sonnet', 'pi'), false);
    assert.equal(modelMatchesRuntime('sonnet', 'traex'), false);
    assert.equal(modelMatchesRuntime('gpt-5.6-sol', 'claude'), false);
    assert.equal(modelMatchesRuntime('anything', 'missing'), false);
    assert.deepEqual(
      resolveRuntimeModelTarget('openai/gpt-4o', 'pi'),
      { runtime: 'pi', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o' },
    );
    assert.deepEqual(
      resolveRuntimeModelTarget('openai/gpt-4o'),
      { runtime: 'codex', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o' },
    );
    assert.deepEqual(
      resolveRuntimeModelTarget('sonnet', 'pi'),
      { runtime: 'claude', model: 'sonnet', canonical: 'sonnet' },
    );
    assert.deepEqual(
      resolveRuntimeModelTarget('openai/gpt-4o', 'traex'),
      { runtime: 'traex', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o' },
    );
    assert.deepEqual(staticModelEntries(), [
      { id: 'sonnet', efforts: getRuntimeDefinition('claude').efforts },
      { id: 'opus', efforts: getRuntimeDefinition('claude').efforts },
      { id: 'haiku', efforts: getRuntimeDefinition('claude').efforts },
      { id: 'fable', efforts: getRuntimeDefinition('claude').efforts },
      { id: 'ark', efforts: getRuntimeDefinition('volc').efforts },
      { id: 'deepseek-official/deepseek-flash', efforts: getRuntimeDefinition('dsh').efforts },
      { id: 'deepseek-official/deepseek-v4-pro', efforts: getRuntimeDefinition('dsh').efforts },
    ]);
  });
});

describe('runtime catalog dependency direction', () => {
  it('keeps adapter constants in the catalog and concrete adapters out of command policy', () => {
    const source = (name) => fs.readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source('runtime-catalog.mjs'), /^\s*import\s|\sfrom\s+['"]/m);
    const commands = source('commands.mjs');
    const coordinator = source('agent-runtime.mjs');
    const compositionRoot = source('runtime-factory.mjs');
    assert.match(commands, /from ['"]\.\/runtime-catalog\.mjs['"]/);
    assert.doesNotMatch(commands, /from ['"]\.\/(?:traex|claude|volc|pi|opencode|agy)\.mjs['"]/);
    assert.doesNotMatch(commands, /\.(?:setModel|setEffort|rotateSession|interruptTurn|deliverBatch)\s*\(/);
    assert.doesNotMatch(coordinator, /\b(?:TRAE?X|CLAUDE|VOLC|PI|DURABLE|OPENCODE|AGY)_(?:MODELS|EFFORTS)\b/);
    assert.doesNotMatch(coordinator, /from ['"]\.\/(?:codex|traex|claude|volc|pi|durable|opencode|agy)\.mjs['"]/);
    for (const adapter of ['codex', 'traex', 'claude', 'volc', 'pi', 'durable', 'opencode', 'agy']) {
      assert.match(compositionRoot, new RegExp(`from ['"]\\./${adapter}\\.mjs['"]`));
    }
    for (const adapter of ['codex.mjs', 'traex.mjs', 'claude.mjs', 'volc.mjs', 'pi.mjs', 'durable.mjs', 'opencode.mjs', 'agy.mjs']) {
      assert.match(source(adapter), /from ['"]\.\/runtime-catalog\.mjs['"]/, adapter);
    }
  });
});
