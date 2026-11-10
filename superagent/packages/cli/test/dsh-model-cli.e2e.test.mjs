// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';
import {
  CLI_DSH_MODEL_MODULE, CLI_ENTRY, PROJECT_ROOT, cliSkipReason, createCliFixture,
} from '../../daemon/test/cli-fixture.mjs';
import { DSH_BRIDGE_PATH } from '../../daemon/src/dsh-runtime.mjs';

// End-to-end coverage of `ov-superagent dsh model ...` through the real CLI entry
// point. The fake `dsh` on PATH is the same fixture the daemon tests use, so
// what is measured is the CLI's own behaviour: argument parsing, the control
// subprocess it spawns, the `ops` it sends, and the files that end up on disk.
//
// The suite skips while `packages/cli/src/dsh-model.mjs` does not exist, which
// is the state before the CLI command group lands. That keeps a source tree
// without the command green instead of red, while the assertions below are the
// acceptance contract it has to satisfy.

// node:test treats any non-false `skip` value as "skip the suite", and a
// skipped suite cancels its subtests; `false` is what actually runs them.
const skip = cliSkipReason() ?? false;

function output(result) {
  return `${result.stdout}\n${result.stderr}`;
}

/** The route profile the settings document must hold. */
function expectRoute(providers, route) {
  assert.ok(providers, 'the profile patch must contain an llm-pi-ai entry');
  const profile = providers[route];
  assert.ok(profile, `the profile patch must declare route ${route}: ${JSON.stringify(providers)}`);
  return profile;
}

describe('ov-superagent dsh model through the real CLI entry point', { skip }, () => {
  /** @type {ReturnType<typeof createCliFixture>} */
  let fixture;
  const baseURL = 'http://127.0.0.1:9/v1';

  // `ov-superagent dsh model add` no longer exists, so the document these cases start
  // from is written directly (JSON is valid YAML, the same shape a fresh home
  // is seeded with). The CLI's own writer is covered by the unit tests and the
  // interactive suite; this file is about the control subprocess and the files
  // it leaves on disk.
  function seedRoutes(providers) {
    const document = fixture.profilePatch('qa');
    fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
    fs.writeFileSync(document, `${JSON.stringify([{ id: 'llm-pi-ai', config: { providers } }])}\n`, { mode: 0o600 });
  }

  before(() => {
    assert.ok(fs.existsSync(CLI_ENTRY), `the CLI entry point must exist: ${CLI_ENTRY}`);
    assert.ok(fs.existsSync(CLI_DSH_MODEL_MODULE), `the dsh model command must exist: ${CLI_DSH_MODEL_MODULE}`);
    fixture = createCliFixture(null);
    seedRoutes({
      deepseek: {
        api: 'openai-completions',
        baseURL: 'http://127.0.0.1:9/v1',
        apiKeyEnv: 'DEEPSEEK_API_KEY',
        displayName: 'DeepSeek Chat',
        models: [
          { id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'], contextWindow: 1_000_000, maxTokens: 384_000 },
          { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner' },
        ],
      },
      local: {
        api: 'openai-completions',
        baseURL: 'http://127.0.0.1:9/other/v1',
        apiKeyEnv: 'LOCAL_KEY',
        displayName: 'Other Model',
        models: [{ id: 'other-model', name: 'Other Model', reasoningEfforts: false }],
      },
    });
  });

  after(() => { fixture?.cleanup(); });

  test('the CLI advertises the command group and its own help', async () => {
    const usage = await fixture.runCli(['--lang', 'en', 'help']);
    assert.equal(usage.code, 0, output(usage));
    assert.match(output(usage), /dsh model/, 'the top-level usage must list the model command group');
    assert.match(output(usage), /\[list\|remove\|import\|repair\|key\]/, 'the group and its real subcommands are listed');
    assert.doesNotMatch(output(usage), /\[list\|add\|/, 'the removed add is not advertised');

    const help = await fixture.runCli(['dsh', 'model', '--help']);
    assert.equal(help.code, 0, output(help));
    for (const subcommand of ['list', 'remove', 'import', 'repair', 'key set', 'key unset']) {
      assert.match(output(help), new RegExp(subcommand), `help must document ${subcommand}: ${output(help)}`);
    }
    // Adding has one shape (the menu), so the usage must not advertise an add.
    assert.doesNotMatch(output(help), /dsh model add/, `help must not advertise the removed add: ${output(help)}`);
    const removed = await fixture.runCli(['dsh', 'model', 'add', 'deepseek/group/x']);
    assert.equal(removed.code, 2, output(removed));
    assert.match(output(removed), /there is no "add" subcommand/);
    // Help must not spawn a control subprocess or need an instance.
    assert.equal(fixture.logEntries().filter((entry) => entry.event === 'spawn').length, 0);
  });

  test('a write goes through the settings service, not into YAML', async () => {
    // `add` is gone; `remove` writes through the same control subprocess and the
    // same settings service, so this is the case that proves the path. The route
    // it removes is seeded here and restored afterwards, so the cases that share
    // the fixture document are unaffected by it.
    const document = fixture.profilePatch('qa');
    const original = fs.readFileSync(document, 'utf8');
    seedRoutes({
      ...fixture.patchProviders('qa'),
      throwaway: {
        api: 'openai-completions', baseURL, apiKeyEnv: 'THROWAWAY_KEY', models: [{ id: 'only-model' }],
      },
    });
    const result = await fixture.runCli(['dsh', 'model', 'remove', 'throwaway/only-model']);
    assert.equal(result.code, 0, output(result));

    // One control subprocess, spawned against this instance's dataDir, with the
    // control patch the design names and no MCP client in it.
    const spawns = fixture.logEntries().filter((entry) => entry.event === 'spawn');
    assert.equal(spawns.length, 1, `exactly one control subprocess: ${JSON.stringify(spawns)}`);
    const spawn = spawns[0];
    assert.deepEqual(spawn.argv.slice(0, 2), ['--profile', 'sdk']);
    assert.equal(spawn.argv[2], '--patch');
    assert.equal(spawn.controlPatch, fixture.controlPatch('qa'));
    assert.ok(fs.existsSync(spawn.controlPatch), 'the control patch must exist while the subprocess runs');
    assert.equal(spawn.controlPatchExists, true);
    assert.equal(spawn.cwd, fixture.dataDirOf('qa'), 'the control subprocess runs in the instance dataDir');
    // The control patch wires the bridge and nothing else: no MCP client, and
    // no llm-pi-ai providers (the settings service owns that document).
    const controlPatch = JSON.parse(fs.readFileSync(spawn.controlPatch, 'utf8'));
    assert.ok(Array.isArray(controlPatch));
    assert.ok(controlPatch.some((entry) => entry.id === 'sdk-jsonrpc-server' && entry.disabled === true));
    const inserted = controlPatch.flatMap((entry) => entry.insert ?? []);
    // The bridge path comes from the checkout the CLI itself runs, which is not
    // necessarily this one (OV_SUPERAGENT_E2E_PROJECT_ROOT); the last segments are the
    // contract.
    const bridgeEntry = inserted.find((entry) => entry.id === 'vika-dsh-bridge');
    assert.ok(bridgeEntry, 'the control patch must insert the vika bridge');
    assert.equal(path.isAbsolute(bridgeEntry.name), true);
    assert.equal(path.basename(bridgeEntry.name), path.basename(DSH_BRIDGE_PATH));
    assert.equal(inserted.some((entry) => entry.id === 'vika-personal-agent'), false,
      'a control subprocess needs no MCP client');
    assert.equal(controlPatch.some((entry) => entry.id === 'llm-pi-ai'), false,
      'the control patch must not overlay the namespace it edits');

    // The CLI translates the request into ops; the settings service is what
    // writes the document, so the YAML on disk is a consequence, never a CLI
    // edit.
    const ops = fixture.mutateOps();
    assert.ok(ops.length > 0, 'the CLI must send settings ops');
    // The route's last model is gone, so the whole route is unset: a removal of
    // one model among several is the `set` case covered by the unit tests.
    assert.deepEqual(
      ops.filter((op) => op.path?.[1] === 'throwaway'),
      [{ op: 'unset', path: ['providers', 'throwaway'] }],
      `the CLI must unset providers.throwaway: ${JSON.stringify(ops)}`,
    );

    // A credential reference is a name, never a value: nothing may leak one.
    const requests = fixture.requests();
    assert.equal(requests.filter((entry) => entry.method === 'vika/credentials/set').length, 0,
      'a removal stores no credential');
    assert.equal(fs.existsSync(fixture.credentialsFile('qa')), false);

    // Put the shared document back for the cases that follow.
    fs.writeFileSync(document, original, { mode: 0o600 });
  });

  test('key set reads the value from stdin and stores only the reference', async () => {
    const result = await fixture.runCli(['dsh', 'model', 'key', 'set', 'DEEPSEEK_API_KEY'], {
      input: 'super-secret-value\n',
    });
    assert.equal(result.code, 0, output(result));
    // The value never appears in the CLI's own output, only the reference.
    assert.match(output(result), /DEEPSEEK_API_KEY/);
    assert.doesNotMatch(output(result), /super-secret-value/);

    const credentials = fixture.credentials('qa');
    assert.ok(credentials.text, 'the credentials document must exist');
    assert.equal(credentials.refs.get('DEEPSEEK_API_KEY'), 'super-secret-value');
    assert.equal(credentials.mode, 0o600, 'a credentials document is owner-only');
    // Written through the bridge, never by the CLI directly.
    assert.ok(fixture.requests('vika/credentials/set').length >= 1);
    const set = fixture.requests('vika/credentials/set').at(-1);
    assert.equal(set.params.ref, 'DEEPSEEK_API_KEY');
    // The fake dsh redacts the value in its own log, so the log proves the
    // reference and the length; the value itself is asserted on disk only.
    assert.equal(set.params.valueLength, 'super-secret-value'.length);
    assert.equal(set.params.value, undefined, 'a credential value must never be logged');
    assert.doesNotMatch(fs.readFileSync(fixture.profilePatch('qa'), 'utf8'), /super-secret-value/);
  });

  test('list --json reports the declared routes and their credential state', async () => {
    const result = await fixture.runCli(['dsh', 'model', 'list', '--json']);
    assert.equal(result.code, 0, output(result));
    const listed = JSON.parse(result.stdout);
    assert.ok(Array.isArray(listed), `list --json must print an array: ${result.stdout}`);
    const byProvider = new Map(listed.map((entry) => [entry.provider, entry]));
    const deepseek = byProvider.get('deepseek');
    assert.ok(deepseek, `the declared route must be listed: ${result.stdout}`);
    assert.equal(deepseek.declared, true);
    assert.equal(deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY');
    // The ref and its presence are the contract; the CLI may add fields of its
    // own (it reports whether the reference is known to the catalogue).
    assert.equal(deepseek.credential.ref, 'DEEPSEEK_API_KEY');
    assert.equal(deepseek.credential.present, true);
    assert.equal(deepseek.credential.known, true);
    assert.deepEqual(deepseek.models.map((model) => model.id).sort(), ['deepseek-chat', 'deepseek-reasoner']);
    assert.equal(deepseek.models.every((model) => model.inCatalog === true), true,
      'a credentialed route lists its models in the catalogue');
    const local = byProvider.get('local');
    assert.equal(local.credential.present, false, 'an unset reference is reported as absent');
    assert.equal(local.models[0].inCatalog, false, 'a model without a credential is declared but not offered');
  });

  test('remove drops one model and then the emptied route', async () => {
    const one = await fixture.runCli(['dsh', 'model', 'remove', 'deepseek/deepseek-reasoner']);
    assert.equal(one.code, 0, output(one));
    let providers = fixture.patchProviders('qa');
    assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['deepseek-chat']);
    assert.ok(providers.local, 'other routes survive a model removal');

    const route = await fixture.runCli(['dsh', 'model', 'remove', 'deepseek']);
    assert.equal(route.code, 0, output(route));
    providers = fixture.patchProviders('qa');
    assert.equal(providers.deepseek, undefined, 'removing a route unsets providers.deepseek');
    assert.deepEqual(Object.keys(providers), ['local']);
  });

  test('key unset removes the stored reference', async () => {
    const result = await fixture.runCli(['dsh', 'model', 'key', 'unset', 'DEEPSEEK_API_KEY']);
    assert.equal(result.code, 0, output(result));
    const credentials = fixture.credentials('qa');
    assert.equal(credentials.refs.has('DEEPSEEK_API_KEY'), false);
    assert.ok(fixture.requests('vika/credentials/unset').length >= 1);
  });

  test('import migrates a legacy models.json, and --dry-run writes nothing', async () => {
    const legacy = {
      providers: {
        legacy: {
          api: 'openai-completions',
          baseUrl: 'http://127.0.0.1:9/legacy/v1',
          apiKey: 'legacy-secret-value',
          models: [{
            id: 'group/legacy-model', name: 'Legacy', input: ['text'],
            contextWindow: 128_000, maxTokens: 8_192, reasoning: true,
            thinkingLevelMap: { low: 'low', high: 'high' },
          }],
        },
      },
    };
    const legacyFile = path.join(fixture.root, 'legacy-models.json');
    fs.writeFileSync(legacyFile, `${JSON.stringify(legacy, null, 2)}\n`);

    const dry = await fixture.runCli(['dsh', 'model', 'import', legacyFile, '--dry-run']);
    assert.equal(dry.code, 0, output(dry));
    assert.match(dry.stdout, /legacy/, `the plan must name the route it would write: ${dry.stdout}`);
    assert.equal(fixture.logEntries().filter((entry) => entry.event === 'spawn'
      && entry.argv.includes(legacyFile)).length, 0, 'a dry run spawns no control subprocess');
    assert.equal(fixture.credentials('qa').refs.has('LEGACY_API_KEY'), false, 'a dry run stores no credential');

    const imported = await fixture.runCli(['dsh', 'model', 'import', legacyFile]);
    assert.equal(imported.code, 0, output(imported));
    const profile = expectRoute(fixture.patchProviders('qa'), 'legacy');
    assert.equal(profile.api, 'openai-completions');
    assert.equal(profile.baseURL, 'http://127.0.0.1:9/legacy/v1');
    assert.equal(profile.apiKeyEnv, 'LEGACY_API_KEY');
    assert.equal(profile.models[0].id, 'group/legacy-model');
    assert.equal(profile.models[0].contextWindow, 128_000);
    // The Pi key travels into the credential store; the profile only names it.
    const credentials = fixture.credentials('qa');
    assert.equal(credentials.refs.get('LEGACY_API_KEY'), 'legacy-secret-value');
    assert.doesNotMatch(fs.readFileSync(fixture.profilePatch('qa'), 'utf8'), /legacy-secret-value/);
    // The migration replaces only the route it names.
    assert.ok(fixture.patchProviders('qa').local, 'routes models.json does not mention survive');
  });

  test('repair backs up a malformed document and makes the namespace readable again', async () => {
    // A hand-edited patch the settings service cannot parse: every
    // settings call fails, so the only way back is the documented repair.
    const document = fixture.profilePatch('qa');
    const healthy = fs.readFileSync(document, 'utf8');
    fs.writeFileSync(document, '- this: [is not, valid json\n');

    const repaired = await fixture.runCli(['dsh', 'model', 'repair']);
    assert.equal(repaired.code, 0, output(repaired));
    // The CLI speaks the operator's language; the report names the document
    // and says it backed the old one up in either language.
    assert.match(repaired.stdout, /备份|backed up/i, `repair must report the backup: ${repaired.stdout}`);
    assert.match(repaired.stdout, /验证|verif/i, `repair must report its verification: ${repaired.stdout}`);
    const backups = fs.readdirSync(path.dirname(document)).filter((name) => name.startsWith(`${path.basename(document)}.bak-`));
    assert.equal(backups.length, 1, `the malformed document must be backed up: ${fs.readdirSync(path.dirname(document))}`);
    const repairedText = fs.readFileSync(document, 'utf8');
    assert.doesNotThrow(() => JSON.parse(repairedText), `repair must leave a parseable document: ${repairedText}`);
    // The namespace answers again: repair ends by asking the service.
    // The minimal document names the entry but declares no providers block.
    assert.equal(fixture.patchProviders('qa'), null, 'the minimal document declares no route');

    // A healthy document is left alone.
    const before = fs.readFileSync(document, 'utf8');
    const noop = await fixture.runCli(['dsh', 'model', 'repair']);
    assert.equal(noop.code, 0, output(noop));
    assert.equal(fs.readFileSync(document, 'utf8'), before, 'repair must not rewrite a readable document');
    // Restore the routes earlier tests left behind for any later case.
    fs.writeFileSync(document, healthy);
  });

  test('a NOT_READY answer during control boot is retried, not surfaced', async () => {
    // The real bridge is wired before the credentials service mounts, so a
    // control client can see -32002 on its first call. The fake refuses the
    // first two service calls to reproduce that window.
    const result = await fixture.runCli(['dsh', 'model', 'list', '--json'], {
      env: { FAKE_DSH_COLD_REFUSALS: '2' },
    });
    assert.equal(result.code, 0, output(result));
    assert.doesNotThrow(() => JSON.parse(result.stdout), `list --json must still answer: ${result.stdout}`);
    assert.equal(fixture.logEntries().filter((entry) => entry.event === 'response' && entry.code === -32002).length >= 2, true,
      'the fixture must have refused the first two service calls');
  });

  test('the removed add subcommand writes nothing, with or without flags', async () => {
    const before = fs.readFileSync(fixture.profilePatch('qa'), 'utf8');
    const spawnsBefore = fixture.logEntries().filter((entry) => entry.event === 'spawn').length;
    for (const args of [
      ['dsh', 'model', 'add', 'invalid-route/model-a', '--api', 'not-a-protocol', '--base-url', baseURL],
      ['dsh', 'model', 'add', 'bare-model-id'],
      ['dsh', 'model', 'add'],
    ]) {
      const result = await fixture.runCli(args);
      assert.equal(result.code, 2, `${args.join(' ')}: ${output(result)}`);
      assert.match(output(result), /there is no "add" subcommand/, output(result));
    }
    assert.equal(fs.readFileSync(fixture.profilePatch('qa'), 'utf8'), before, 'a refused add writes nothing');
    assert.equal(fixture.logEntries().filter((entry) => entry.event === 'spawn').length, spawnsBefore,
      'a refused add spawns no control subprocess');
  });

  test('an unknown route fails without writing', async () => {
    const before = fs.readFileSync(fixture.profilePatch('qa'), 'utf8');
    const unknown = await fixture.runCli(['dsh', 'model', 'remove', 'does-not-exist']);
    assert.notEqual(unknown.code, 0, `removing an unknown route must be refused: ${output(unknown)}`);
    assert.equal(fs.readFileSync(fixture.profilePatch('qa'), 'utf8'), before, 'a refused command writes nothing');
  });

  test('every command works on a named instance instead of the default one', async () => {
    const other = createCliFixture(null, { instances: ['qa', 'second'] });
    try {
      // The document is seeded directly (there is no add subcommand any more);
      // what is measured is which instance the control subprocess starts for.
      const document = other.profilePatch('second');
      fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
      fs.writeFileSync(document, `${JSON.stringify([{ id: 'llm-pi-ai', config: { providers: {
        'second-route': {
          api: 'openai-completions', baseURL, apiKeyEnv: 'SECOND_KEY', models: [{ id: 'model-a' }],
        },
      } } }])}\n`, { mode: 0o600 });
      const result = await other.runCli(['dsh', 'model', 'key', 'set', 'SECOND_KEY', '--instance', 'second'], {
        input: 'sk-second\n',
      });
      assert.equal(result.code, 0, output(result));
      assert.equal(other.credentials('second').refs.get('SECOND_KEY'), 'sk-second');
      assert.equal(other.credentials('qa').refs.has('SECOND_KEY'), false, 'the default instance is untouched');
      assert.equal(fs.existsSync(other.controlPatch('qa')), false, 'only the named instance spawned a control subprocess');
      assert.equal(fs.existsSync(other.controlPatch('second')), true);
    } finally {
      other.cleanup();
    }
  });
});
