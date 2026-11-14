import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { CommandConsole } from '@openviking/react/components/command-console';

const commands = [
  {
    name: 'read',
    inputText: 'host read',
    aliases: ['cat'],
    description: 'Read a resource',
    usage: 'host read <uri>',
  },
];
const props = { records: [], value: '', commands, onValueChange() {} };
const commandText =
  '<span class="ov-command-console__command-prefix">host </span><span class="ov-command-console__command-name">read</span>';

test('record echoes canonical host prefixes and preserves arguments and unknown text', () => {
  const record = {
    id: 'echo',
    command: '  /cat "viking://demo/a b" --offset 2',
    status: 'success',
    createdAt: '10:24',
  };
  const renderBubble = (commands) =>
    renderMarkup(
      createElement(CommandConsole, { ...props, commands, records: [record] }),
    ).match(/<div class="ov-command-console__bubble">([\s\S]*?)<\/div>/)[1];
  const bubble = renderBubble(commands).replace(/<a\b[^>]*>|<\/a>/g, '');
  assert.ok(
    bubble.includes(
      `ov-command-console__bubble-command">${commandText}</span> "viking://demo/a b" --offset 2`,
    ),
  );
  assert.ok(bubble.includes('ov-command-console__bubble-time">10:24</span>'));
  const unknown = renderBubble([]).replace(/<a\b[^>]*>|<\/a>/g, '');
  assert.ok(unknown.includes('  /cat "viking://demo/a b" --offset 2'));
  assert.ok(!unknown.includes('ov-command-console__bubble-command'));
});

test('command presentation preserves single and multiword host command text', () => {
  for (const [name, inputText] of [
    ['read', 'read'],
    ['read all', 'tool config read all'],
  ]) {
    const command = { name, inputText, description: name, usage: inputText };
    const value = `  ${inputText} viking://demo`;
    const html = renderMarkup(
      createElement(CommandConsole, {
        ...props,
        commands: [command],
        value,
      }),
    );
    const editor = html.match(
      /<span class="ov-command-console__input-command">([\s\S]*?)<\/span><\/span>/,
    )[1];
    assert.equal(editor.replace(/<[^>]+>/g, ''), inputText);
    assert.ok(editor.includes(`ov-command-console__command-name">${name}`));
    assert.match(
      html,
      /<textarea[^>]*> {2}(?:read|tool config read all) viking:\/\/demo<\/textarea>/,
    );
  }
});
