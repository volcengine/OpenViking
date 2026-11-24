import assert from 'node:assert/strict';
import test from 'node:test';
import {
  parseWikiFileConflicts,
  locateWikiConflict,
} from './helpers/internal-hooks.mjs';

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

test('Wiki conflict anchors exclude YAML frontmatter closed with a document end marker', () => {
  const header = '\uFEFF--- \r\npolicy: two approvals\r\n... \r\n';
  const beforeAnchor = `${header}\r\n---\r\n`;
  const body = 'two approvals';
  const parsed = parseWikiFileConflicts(
    `${beforeAnchor}${body}\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(record)}\n-->`,
  );
  assert.deepEqual(parsed.records[0].location, {
    status: 'unique',
    start: beforeAnchor.length,
    end: beforeAnchor.length + body.length,
    line: 6,
  });
  assert.deepEqual(locateWikiConflict(`${header}# Body`, body), {
    status: 'missing',
  });
});

test('Wiki conflict anchors keep Markdown that only resembles YAML frontmatter', () => {
  const matchText = 'two approvals';
  for (const text of [
    `This policy requires ${matchText}.`,
    `policy: [${matchText}`,
  ]) {
    const markdown = `---\n${text}\n...\n# Body`;
    const start = markdown.indexOf(matchText);
    assert.deepEqual(locateWikiConflict(markdown, matchText), {
      status: 'unique',
      start,
      end: start + matchText.length,
      line: 2,
    });
  }
});

test('Wiki conflict anchors prefer exact hits and map soft wraps back to source offsets', () => {
  const snippet = '金额\r\n> 上限';
  const prefix = '# 规则\r\n\r\n> ';
  const body = prefix + snippet;
  assert.deepEqual(locateWikiConflict(body, '金额上限'), {
    status: 'unique',
    start: prefix.length,
    end: body.length,
    line: 3,
  });
  const joined = '金额上限';
  assert.equal(
    locateWikiConflict(`${body}\n\n${snippet}\n\n${joined}`, joined).line,
    9,
  );
  assert.equal(
    locateWikiConflict(`${body}\n\n金额\n上限`, joined).status,
    'ambiguous',
  );
});

test('Wiki conflict soft wraps preserve words, block boundaries and formatting', () => {
  for (const [body, anchor, status] of [
    ['Needs two\n  approvals', 'Needs two approvals', 'unique'],
    ['Needs two\n  approvals', 'Needs twoapprovals', 'missing'],
    ['За\nчем он следит?', 'Зачем он следит?', 'missing'],
    ['> 金额\n    > 上限', '金额 > 上限', 'unique'],
    ['法规\n\n更新', '法规更新', 'missing'],
    ['法规  \n更新', '法规更新', 'missing'],
    ['**法规\n更新**', '法规更新', 'missing'],
    ['**法规\n更新**', '**法规更新**', 'unique'],
    ['<i>法规\n更新</i>', '法规更新', 'missing'],
    ['参考 [法\n规](</new url>)', '[法规](</new url>)', 'unique'],
    ['参考 [法规](</new\nurl>)', '[法规](</new url>)', 'missing'],
    ['参考 [法\n规]\n\n[法 规]: /new\n[法规]: /old', '[法规]', 'missing'],
    ['配置 `键  \r\n 名` 已生效', '`键    名`', 'unique'],
    ['配置 `键\n名` 已生效', '`键名`', 'missing'],
  ]) {
    assert.equal(locateWikiConflict(body, anchor).status, status, body);
  }
});

test('Wiki metadata distinguishes trailing comments from Markdown examples', () => {
  const body = 'two approvals';
  const trailer = `<!-- CONFLICTS_CONTENT\n${JSON.stringify(record)}\n-->`;
  for (const example of [
    `\\${trailer}`,
    `\`${trailer}\``,
    `\n    ${trailer}`,
    `>     ${trailer}`,
    `\`\`\`md\n${trailer}\n\`\`\``,
  ]) {
    const document = `${body}\n\n${example}`;
    assert.equal(parseWikiFileConflicts(document).status, 'missing', example);
  }
  const prefix = `${body}\n\n\`\`\`text\n`;
  const legacy = parseWikiFileConflicts(prefix + trailer);
  assert.equal(legacy.document, prefix);
  assert.equal(legacy.records[0].location.status, 'unique');
  assert.equal(
    locateWikiConflict('\\<!-- 示例\n<!-- 审核中 -->\n已发布', '审核中').status,
    'missing',
  );
  assert.equal(
    locateWikiConflict('<textarea>\n<!-- 字面语法\n</textarea>', '字面语法')
      .status,
    'unique',
  );
});
