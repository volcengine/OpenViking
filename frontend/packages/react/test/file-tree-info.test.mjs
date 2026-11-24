import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolveFileTreeInfoKey,
  showsInlineDescription,
  splitVikingUri,
  resolveInfoCopy,
} from '@openviking/react/components/file-tree';

test('splits a viking URI into lower-cased segments', () => {
  assert.deepEqual(splitVikingUri('viking://user/Alice/memories/'), [
    'user',
    'alice',
    'memories',
  ]);
  assert.deepEqual(splitVikingUri('viking://resources?limit=10'), [
    'resources',
  ]);
  assert.deepEqual(splitVikingUri(''), []);
});

test('resolves the user root for both shapes', () => {
  assert.equal(resolveFileTreeInfoKey('viking://user'), 'userRoot');
  assert.equal(resolveFileTreeInfoKey('viking://user/alice'), 'userRoot');
  assert.equal(resolveFileTreeInfoKey('viking://users/alice'), 'userRoot');
});

test('resolves user-scope directories by their third segment', () => {
  const expected = {
    memories: 'memories',
    peers: 'peers',
    privacy: 'privacy',
    resources: 'userResources',
    sessions: 'sessions',
    skills: 'skills',
  };

  for (const [segment, key] of Object.entries(expected)) {
    assert.equal(resolveFileTreeInfoKey(`viking://user/alice/${segment}`), key);
  }
});

test('resolves memory children only at the fourth segment', () => {
  assert.equal(
    resolveFileTreeInfoKey('viking://user/alice/memories/profile.md'),
    'profile',
  );
  assert.equal(
    resolveFileTreeInfoKey('viking://user/alice/memories/experiences'),
    'experiences',
  );
  assert.equal(
    resolveFileTreeInfoKey('viking://user/alice/memories/unknown'),
    null,
  );
  // 更深一层不再判定，避免把记忆目录下的普通文件当成语义节点。
  assert.equal(
    resolveFileTreeInfoKey('viking://user/alice/memories/events/2026'),
    null,
  );
});

test('does not apply memory rules under other user directories', () => {
  assert.equal(
    resolveFileTreeInfoKey('viking://user/alice/resources/profile.md'),
    null,
  );
});

test('resolves top-level roots and shared resources', () => {
  assert.equal(resolveFileTreeInfoKey('viking://resources'), 'sharedResources');
  assert.equal(resolveFileTreeInfoKey('viking://agent'), 'agentRoot');
  assert.equal(resolveFileTreeInfoKey('viking://agents'), 'agentRoot');
  assert.equal(resolveFileTreeInfoKey('viking://memory'), 'memoryRoot');
  assert.equal(resolveFileTreeInfoKey('viking://session'), 'sessionRoot');
  assert.equal(resolveFileTreeInfoKey('viking://system'), 'systemRoot');
  assert.equal(resolveFileTreeInfoKey('viking://unknown'), null);
  assert.equal(resolveFileTreeInfoKey('viking://resources/docs'), null);
});

test('keeps memory children out of inline descriptions', () => {
  assert.equal(showsInlineDescription('profile'), false);
  assert.equal(showsInlineDescription('experiences'), false);
  assert.equal(showsInlineDescription('memories'), true);
  assert.equal(showsInlineDescription('systemRoot'), true);
  assert.equal(showsInlineDescription(null), false);
});

test('falls back to built-in copy per key', () => {
  const builtIn = resolveInfoCopy('memories', undefined);

  assert.equal(typeof builtIn.description, 'string');
  assert.equal(typeof builtIn.triggerBehavior, 'string');
});

test('lets a host override one key without touching the others', () => {
  const copy = resolveInfoCopy('memories', {
    description: { memories: 'Custom' },
  });

  assert.equal(copy.description, 'Custom');
  assert.equal(typeof copy.triggerBehavior, 'string');
});

test('treats an empty host string as hidden', () => {
  const copy = resolveInfoCopy('userRoot', {
    description: { userRoot: '' },
    triggerBehavior: { userRoot: '' },
  });

  assert.equal(copy.description, undefined);
  assert.equal(copy.triggerBehavior, undefined);
});

test('omits trigger copy for keys that have none', () => {
  const copy = resolveInfoCopy('systemRoot', undefined);

  assert.equal(typeof copy.description, 'string');
  assert.equal(copy.triggerBehavior, undefined);
});
