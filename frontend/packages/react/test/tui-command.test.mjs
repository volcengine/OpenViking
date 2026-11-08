import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';

// Exercise the command metadata without adding it to the public package entry.
const output = await mkdtemp(join(tmpdir(), 'ov-tui-command-'));
const builder = await createRslib({
  config: {
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            commands: fileURLToPath(
              new URL(
                '../src/components/tui/command-definitions.ts',
                import.meta.url,
              ),
            ),
            catalog: fileURLToPath(
              new URL(
                '../src/components/tui/command-catalog.ts',
                import.meta.url,
              ),
            ),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const { findTuiCommand, groupTuiCommands, TUI_COMMANDS } = await import(
  pathToFileURL(join(output, 'commands.js'))
);
const { parseTuiCommandInput, resolveTuiCommandName, resolveTuiSkeleton } =
  await import(pathToFileURL(join(output, 'catalog.js')));
after(() => rm(output, { recursive: true, force: true }));

test('parses a slash command with arguments', () => {
  assert.deepEqual(parseTuiCommandInput('/find "release notes"'), {
    name: 'find',
    rest: ' "release notes"',
    hasTrailingSpace: true,
  });
  assert.deepEqual(parseTuiCommandInput('/read'), {
    name: 'read',
    rest: '',
    hasTrailingSpace: false,
  });
  assert.equal(parseTuiCommandInput('find docs'), null);
});

test('resolves command names for records without a leading slash', () => {
  assert.equal(resolveTuiCommandName('/find "notes"'), 'find');
  assert.equal(resolveTuiCommandName('  /TREE viking://  '), 'tree');
  assert.equal(resolveTuiCommandName('stat'), 'stat');
});

test('finds commands by name and alias', () => {
  assert.equal(findTuiCommand('find')?.name, 'find');
  assert.equal(findTuiCommand('/list')?.name, 'ls');
  assert.equal(findTuiCommand('rename')?.name, 'mv');
  assert.equal(findTuiCommand('nope'), undefined);
});

test('groups commands without empty sections', () => {
  const sections = groupTuiCommands(TUI_COMMANDS);
  assert.ok(sections.length > 0);
  assert.ok(sections.every((section) => section.commands.length > 0));
  assert.equal(
    sections.reduce((total, section) => total + section.commands.length, 0),
    TUI_COMMANDS.length,
  );
});

test('selects pending skeletons by command', () => {
  assert.equal(
    resolveTuiSkeleton('/find notes', findTuiCommand('find')),
    'search',
  );
  assert.equal(
    resolveTuiSkeleton('/ls viking://', findTuiCommand('ls')),
    'tree',
  );
  assert.equal(
    resolveTuiSkeleton('/stat viking://', findTuiCommand('stat')),
    'status',
  );
  assert.equal(
    resolveTuiSkeleton('/rm viking://a', findTuiCommand('rm')),
    'write',
  );
  assert.equal(
    resolveTuiSkeleton('/read viking://a', findTuiCommand('read')),
    'resource',
  );
  assert.equal(resolveTuiSkeleton('/unknown', undefined), 'generic');
});
