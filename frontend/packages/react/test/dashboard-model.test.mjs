import assert from 'node:assert/strict';
import test from 'node:test';
import {
  parseWikiFileConflicts,
  locateWikiConflict,
} from '../src/modules/file-browser/wiki-conflicts.ts';

const record = {
  match_text: 'two approvals',
  problem_description: 'Approval policy differs',
  reason: 'Two references disagree',
  severity: 'high',
  state: 'unfixed',
  begin_time: 100,
  fix_time: null,
  fix_plan: 'Verify policy',
  resolution_basis: '',
};
const content = (value) =>
  `# Policy\n\ntwo approvals\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(value)}\n-->`;

test('native Wiki footer preserves unresolved/fixed state and rejects malformed metadata', () => {
  const parsed = parseWikiFileConflicts(content(record));
  assert.equal(parsed.status, 'available');
  assert.equal(parsed.records[0].location.line, 3);
  assert.equal(parsed.records[0].state, 'unfixed');
  assert.equal(
    parseWikiFileConflicts(
      content({ ...record, state: 'fixed', fix_time: 200 }),
    ).records[0].state,
    'fixed',
  );
  assert.equal(
    parseWikiFileConflicts(content({ ...record, state: 'fixed', fix_time: 99 }))
      .status,
    'invalid',
  );
  assert.equal(
    parseWikiFileConflicts('<!-- CONFLICTS_CONTENT {broken -->').status,
    'invalid',
  );
  assert.equal(parseWikiFileConflicts('# Ordinary document').status, 'missing');
  assert.equal(
    locateWikiConflict(
      '---\npolicy: two approvals\n---\n<!-- two approvals -->\ntwo approvals',
      'two approvals',
    ).status,
    'unique',
  );
  assert.equal(
    locateWikiConflict('two approvals\ntwo approvals', 'two approvals').status,
    'ambiguous',
  );
});

test('native conflict strings may contain CLI flags and Markdown separators', () => {
  const value = {
    ...record,
    match_text: 'Run --force',
    reason: 'A --- separator is ordinary JSON text',
  };
  const parsed = parseWikiFileConflicts(
    `Run --force\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(value)}\n-->`,
  );
  assert.equal(parsed.status, 'available');
  assert.equal(parsed.records[0].location.status, 'unique');
  assert.equal(parsed.records[0].reason, value.reason);
});
