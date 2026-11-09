// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { describe, it } from 'node:test';
import { CodexAgent } from '../src/codex.mjs';
import {
  DEFAULT_PROMPT_DIR,
  assembleConversationPolicy,
  assembleVolcConversationPolicy,
  assembleVolcSelfHostedConversationPolicy,
  docCommentNotice,
  resolveRolePromptPath,
  rolePrompt,
  substitutePromptPlaceholders,
} from '../src/conversation-policy.mjs';
import { agentCliCommands, cliCommandCode } from '../src/agent-clis.mjs';

const { ROLE_PRESETS } = createRequire(import.meta.url)('../../../deployment-config.cjs');

const read = (name) => fs.readFileSync(path.join(DEFAULT_PROMPT_DIR, name), 'utf8');
const role = (name) => resolveRolePromptPath(name);

function withRoleFile(body, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-role-'));
  const filePath = path.join(directory, 'role.md');
  fs.writeFileSync(filePath, body);
  try {
    return callback(filePath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

describe('conversation policy assemble', () => {
  it('assembles one Core for every connector set, with a section per connector in use', () => {
    const order = ['## Lark', '## Slack', '## Weixin', '## Telegram', '## QQ', '## OneBot', '## Email'];
    for (const connectors of [['lark'], ['weixin'], ['telegram', 'email'], ['slack'], ['qq'], ['qq', 'email'], ['slack', 'qq'], ['onebot'],
      ['qq', 'onebot'], ['lark', 'slack', 'weixin', 'telegram', 'qq', 'onebot', 'email']]) {
      const text = assembleConversationPolicy({ connectors, agentName: 'Vee' });
      const label = connectors.join('+');
      assert.ok(text.startsWith('# Operating policy\n'), label);
      assert.match(text, /## Events from other sources/, label);
      assert.match(text, /## OpenViking memory/, label);
      const present = order.filter((heading) => new RegExp(`^${heading}$`, 'm').test(text));
      assert.deepEqual(present, order.filter((heading) => connectors.includes(heading.slice(3).toLowerCase())), label);
      assert.deepEqual(present.map((heading) => text.indexOf(`\n${heading}\n`)), present.map((heading) => text.indexOf(`\n${heading}\n`)).sort((a, b) => a - b), label);
    }
    // Lark's own rules stay with Lark.
    const weixin = assembleConversationPolicy({ connectors: ['weixin'] });
    assert.doesNotMatch(weixin, /## Lark|ca_|om_|image_key/);
    assert.match(assembleConversationPolicy({ connectors: ['lark'] }), /never pass `--profile`/);
  });

  it('keeps QQ owner routing and reply limits in single and mixed sessions', () => {
    for (const connectors of [['qq'], ['qq', 'email'], ['lark', 'slack', 'telegram', 'qq']]) {
      const text = assembleConversationPolicy({ connectors, rolePromptPath: role('assistant') });
      assert.match(text, /^## QQ$/m);
      assert.match(text, /source=qq/);
      assert.match(text, /4000 characters/);
      assert.match(text, /four passive replies/);
      assert.match(text, /Never bypass a reply limit/);
      assert.ok(text.indexOf('## QQ') < text.indexOf('## Role'));
    }
    const single = assembleConversationPolicy({ connectors: ['qq', 'email'] });
    assert.doesNotMatch(single, /voice transcript|^## (Lark|Slack|Telegram|Weixin|OneBot)$/m);
    assert.doesNotMatch(assembleConversationPolicy({ connectors: ['lark'] }), /^## QQ$/m);
  });

  it('explains OneBot batches, the account\'s other programs and other people\'s direct chats', () => {
    for (const connectors of [['onebot'], ['qq', 'onebot', 'email']]) {
      const text = assembleConversationPolicy({ connectors, rolePromptPath: role('assistant') });
      assert.match(text, /^## OneBot$/m);
      assert.match(text, /source=onebot/);
      assert.match(text, /decide once, on the newest/);
      assert.match(text, /On `source=onebot`, `sender=bot` lines appear in groups only, and every one was sent from this same account/);
      assert.match(text, /`owner=1` still marks the owner's/);
      assert.match(text, /plain text: no Markdown/);
      assert.match(text, /`mention_user_ids` is not available/);
      assert.ok(text.indexOf('## OneBot') < text.indexOf('## Role'));
    }
    assert.doesNotMatch(assembleConversationPolicy({ connectors: ['qq'] }), /^## OneBot$/m);
  });

  it('delivers only through the Core tools and keeps the final text internal', () => {
    const text = assembleConversationPolicy({ connectors: ['lark', 'weixin'] });
    assert.match(text, /`send_message`/);
    assert.match(text, /never through a CLI or any other path/);
    assert.match(text, /Your final answer text, your thinking, and anything outside tool calls are internal telemetry that nobody sees/);
    assert.match(text, /If nothing is needed, call no delivery tool for it\./);
    assert.match(text, /Notify only when the owner should know now; otherwise deliver nothing\./);
    assert.doesNotMatch(text, /DELIVERED|NO_DELIVERY|do nothing and end the turn|standard agent protocol/);
  });

  it('fills in the instance name and upgrade target', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'], agentName: '贝塔', upgradeTarget: 'beta' });
    assert.match(text, /directly calls `贝塔`/);
    assert.match(text, /`vika upgrade beta`/);
    assert.doesNotMatch(text, /\{\{[A-Z_]+\}\}/);
  });

  it('explains the authenticated owner marker', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'] });
    assert.match(text, /`owner=1` means the daemon verified the sender as the owner/);
    assert.match(text, /made only at the owner's request \(`owner=1`\) or in a chat the Role names/);
  });

  it('routes @mentions by the authenticated mention= marker, by name only without it', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'], agentName: 'Vika' });
    assert.match(text, /`mention=` says, by id, whom the message's @mentions call/);
    assert.match(text, /only then compare a visible @name with your name/);
    assert.match(text, /`mention=me` or `mention=mixed`, a visible @mention matching your name when there is no `mention=`/);
    assert.match(text, /`mention=` says whether one of them is you/);
  });

  it('places an answer where its message is, never by style', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'], rolePromptPath: role('qa') });
    assert.match(text, /A message with `thread=` is answered inside that thread/);
    assert.match(text, /A message without `thread=` is answered in its conversation/);
    assert.match(text, /answer the original message where it was asked/);
    assert.match(text, /In a topic group \(话题群\) every post is its own topic/);
    // A plain send was once the default reply, and the Q&A role split long answers
    // between a thread and the main conversation: both sent thread answers astray.
    assert.doesNotMatch(text, /Use it for most replies|one-line conclusion in the main conversation|move side discussions into a thread/);
  });

  it('counts a delivery the request asks for elsewhere as work, never as the answer, in both delivery modes', () => {
    for (const stagedReplies of [false, true]) {
      const text = assembleConversationPolicy({ connectors: ['lark'], stagedReplies });
      // An errand elsewhere (a DM, another group) must not use up the one final
      // delivery and leave the person who asked without an answer.
      assert.match(text, /The final delivery is the answer, where the request was made\. A delivery the request itself asks for in another conversation/, `staged=${stagedReplies}`);
      assert.match(text, /is part of the work, not the answer: make it, then still answer where you were asked/, `staged=${stagedReplies}`);
      assert.doesNotMatch(text, /Keep every message where the request is/, `staged=${stagedReplies}`);
    }
  });

  it('keeps the document comment rules out of the policy, in a one-line notice that only comments get', () => {
    for (const stagedReplies of [false, true]) {
      const text = assembleConversationPolicy({ connectors: ['lark'], stagedReplies });
      // Seen by every chat turn, the comment lengths and documents leaked into chat answers.
      assert.doesNotMatch(text, /200 characters|docs \+create|drive \+member-add/, `staged=${stagedReplies}`);
      assert.match(text, /Each comment input is followed by a `\[daemon notice\]` saying how to answer it\./, `staged=${stagedReplies}`);
    }
    const notice = docCommentNotice({ larkCliBin: '/opt/lark-cli' });
    assert.equal(notice.includes('\n'), false);
    assert.match(notice, /^\[daemon notice\] How to answer the document comment above\. These rules are for document comments only, never for chats or topics\./);
    assert.match(notice, /at most about 200 characters .* Up to about 1000 characters: no document\./);
    assert.match(notice, /`\/opt\/lark-cli` as the bot .*drive \+member-add --member-type openid --perm view --yes/);
    assert.doesNotMatch(notice, /<!--|\{\{|interim/);
  });

  it('adds the meeting notes only on the instance that stores them, after OpenViking, leaving the policy as it was otherwise', () => {
    const off = assembleConversationPolicy({ connectors: ['lark'] });
    assert.doesNotMatch(off, /## Meeting notes/);
    const on = assembleConversationPolicy({ connectors: ['lark'], meetingNotes: true });
    assert.ok(on.indexOf('## Meeting notes') > on.indexOf('## OpenViking memory'));
    assert.match(on, /`read_meeting_note`/);
    assert.equal(on.replace(`\n\n${substitutePromptPlaceholders(read('meeting-notes.md')).trim()}`, ''), off);
  });

  it('reads with the owner\'s identity by default; a bot-only instance is told it reads only the chats the bot has joined', () => {
    const owner = assembleConversationPolicy({ connectors: ['lark'] });
    assert.equal(assembleConversationPolicy({ connectors: ['lark'], larkMessageAccess: 'owner' }), owner);
    assert.match(owner, /`search_messages` searches with the owner's identity where the connector supports it\./);
    assert.match(owner, /Anything read with the owner's identity \(`search_messages`, or `as: "user"`\) is the owner's private data/);
    assert.match(owner, /take `as: "user"` to read chats the bot has not joined, with the owner's visibility\./);
    const bot = assembleConversationPolicy({ connectors: ['lark'], larkMessageAccess: 'bot' });
    assert.doesNotMatch(bot, /as: "user"|owner's identity where|owner's visibility|\{\{[A-Z_]+\}\}/);
    assert.match(bot, /`search_messages` searches the chats the bot has joined\./);
    assert.match(bot, /read only chats the bot has joined, as the bot: this instance never reads with the owner's identity/);
  });

  it('adds the mail rules only when a mailbox is connected', () => {
    assert.doesNotMatch(assembleConversationPolicy({ connectors: ['lark'] }), /^## Email$/m);
    for (const connectors of [['lark', 'email'], ['weixin', 'email']]) {
      const text = assembleConversationPolicy({ connectors });
      assert.match(text, /^## Email$/m);
      assert.match(text, /You never send mail\./);
    }
  });

  it('ships no deployment-specific chats or bots in the release prompts', () => {
    const files = ['core.md', 'events.md', 'openviking.md', 'meeting-notes.md', 'natural-writing.md', 'delivery/single.md', 'delivery/staged.md',
      ...fs.readdirSync(path.join(DEFAULT_PROMPT_DIR, 'connectors')).map((name) => path.join('connectors', name)),
      ...fs.readdirSync(path.join(DEFAULT_PROMPT_DIR, 'roles')).map((name) => path.join('roles', name))];
    for (const name of files) {
      assert.doesNotMatch(read(name), /oc_[0-9a-f]{8,}|OpenViking小助手/, name);
    }
  });

  it('advertises every bundled CLI from the same catalog as the runtime PATH', () => {
    const text = assembleConversationPolicy({ connectors: ['weixin'] });
    assert.match(text, /## Bundled command-line tools/);
    for (const { name, bin } of agentCliCommands()) assert.ok(text.includes(`- ${name}: ${cliCommandCode(bin)}`));
    assert.match(text, /messages still go through the Core tools/);
  });

  it('renders executable overrides safely in discovery, Lark and meeting prompts even after PATH is reset', (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-cli-prompt-'));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const workspace = path.join(root, 'workspace');
    fs.mkdirSync(workspace);
    // Include shell expansion, quote, Markdown delimiter and PATH delimiter characters.
    const larkCliBin = path.join(root, "custom tools:'$HOME`", "renamed-lark-$&");
    const slackCliBin = path.join(root, 'custom slack', 'renamed-slack');
    for (const bin of [larkCliBin, slackCliBin]) {
      fs.mkdirSync(path.dirname(bin), { recursive: true });
      fs.writeFileSync(bin, '#!/bin/sh\nprintf "%s:%s\\n" "$1" "$LARKSUITE_CLI_PROFILE"\n', { mode: 0o755 });
    }
    const text = assembleConversationPolicy({ larkCliBin, slackCliBin, meetingNotes: true });
    assert.doesNotMatch(text, /\{\{LARK_CLI/);
    assert.match(text, /never pass `--profile`/);
    assert.match(text, /auth, login, config, or profile commands/);
    assert.ok(text.includes(`read the live document with ${cliCommandCode(larkCliBin)}`));
    for (const bin of [larkCliBin, slackCliBin]) {
      assert.ok(text.includes(cliCommandCode(bin)));
      const code = cliCommandCode(bin, ' --help');
      const fence = code.match(/^`+/)[0];
      const command = code.slice(fence.length, -fence.length);
      const result = spawnSync('/bin/sh', ['-c', command], {
        env: { HOME: root, PATH: '/usr/bin:/bin', LARKSUITE_CLI_PROFILE: 'fixed' }, cwd: workspace, encoding: 'utf8',
      });
      assert.ifError(result.error);
      assert.equal(result.status, 0, result.stderr);
      assert.equal(result.stdout, '--help:fixed\n');
    }
  });
});

describe('the instance role', () => {
  it('ships exactly the presets the manifest accepts, each in English with its reply language', () => {
    assert.deepEqual(fs.readdirSync(path.join(DEFAULT_PROMPT_DIR, 'roles')).sort(), ROLE_PRESETS.map((name) => `${name}.md`).sort());
    for (const name of ROLE_PRESETS) {
      const text = read(path.join('roles', `${name}.md`));
      assert.match(text, /Reply in Chinese by default, or in the language/, name);
      assert.match(text, /OpenViking memory/, name);
      assert.doesNotMatch(text, /【|\{\{(?!AGENT_NAME\}\})/, name);
    }
    assert.match(read('roles/colleague.md'), /AI counterpart/);
    assert.match(read('roles/assistant.md'), /would knowing it now change what they do next\?/);
    assert.match(read('roles/qa.md'), /### Off-topic questions/);
  });

  it('goes after the Core, inside a Role section closed by its boundary', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'], agentName: 'Vee', rolePromptPath: role('assistant') });
    const start = text.indexOf('\n## Role\n');
    assert.ok(start > text.indexOf('## OpenViking memory'));
    assert.match(text, /You are Vee, the owner's personal assistant\./);
    assert.ok(text.indexOf('Role boundary:') > text.indexOf('### Voice', start));
    assert.equal(text.split('\n## Role\n').length, 2);
  });

  it('reads the owner\'s own file, without its comments', () => {
    withRoleFile('<!-- notes for whoever edits this -->\nYou are {{AGENT_NAME}}, the garden club\'s helper.\n', (filePath) => {
      const text = assembleConversationPolicy({ connectors: ['weixin'], agentName: 'Fern', rolePromptPath: filePath });
      assert.match(text, /## Role\n\nThe person who runs this agent wrote the text below\.[^\n]*\n\nYou are Fern, the garden club's helper\.\n\nRole boundary:/);
      assert.doesNotMatch(text, /notes for whoever edits this/);
    });
    withRoleFile('<!-- nothing yet -->\n', (filePath) => {
      assert.equal(rolePrompt({ rolePromptPath: filePath }), '');
      assert.doesNotMatch(assembleConversationPolicy({ rolePromptPath: filePath }), /## Role/);
    });
  });

  it('runs on the Core defaults without a role', () => {
    const text = assembleConversationPolicy({ connectors: ['lark'] });
    assert.doesNotMatch(text, /## Role/);
    assert.match(text, /The Role decides when you speak\. Where it says nothing, use these defaults:/);
    assert.match(text, /Reply in Chinese by default, or in the language the person wrote in\./);
  });
});

describe('natural writing', () => {
  it('follows the role unless the instance turns it off', () => {
    const config = { connectors: ['lark'], rolePromptPath: role('colleague') };
    const on = assembleConversationPolicy(config);
    assert.ok(on.indexOf('## Natural writing') > on.indexOf('Role boundary:'));
    assert.match(assembleConversationPolicy({ connectors: ['lark'] }), /## Natural writing/);
    const off = assembleConversationPolicy({ ...config, naturalWriting: false });
    assert.doesNotMatch(off, /## Natural writing/);
    assert.equal(off, on.replace(`\n\n${read('natural-writing.md').trim()}`, ''));
  });
});

describe('explicit silence is opt-in per instance', () => {
  const base = { workspace: '/srv/vika/workspace', agentName: 'Tester', rolePromptPath: role('colleague') };
  const sessions = [['lark'], ['lark', 'weixin', 'telegram', 'qq', 'email'], ['weixin'], ['telegram', 'email']]
    .map((connectors) => ({ ...base, connectors }));
  const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
  const codexHash = (config) => new CodexAgent(config, {
    getCognitionState: () => ({ runtime: 'codex', epoch: 1, authorityToken: 'token' }), getSetting: () => null,
  }, quiet).policyHash;

  it('assembles the same policy text and policyHash as without the tool when the switch is off', () => {
    for (const config of sessions) {
      const off = assembleConversationPolicy({ ...config, explicitSilence: false });
      assert.equal(off, assembleConversationPolicy(config));
      assert.equal(codexHash({ ...config, explicitSilence: false }), codexHash(config));
      assert.notEqual(codexHash({ ...config, explicitSilence: true }), codexHash(config));
      assert.doesNotMatch(off, /stay_silent|undecided/);
    }
  });

  it('appends one explicit silence section, after the role, when the switch is on', () => {
    const configs = [...sessions, { ...base, connectors: ['telegram'] }, { ...base, connectors: ['lark', 'weixin'] },
      { ...base, connectors: ['slack'], naturalWriting: false }, { connectors: ['weixin'] }];
    for (const config of configs) {
      const off = assembleConversationPolicy(config);
      const on = assembleConversationPolicy({ ...config, explicitSilence: true });
      const label = config.connectors.join('+');
      assert.ok(on.startsWith(`${off}\n\n## Explicit silence\n`), label);
      assert.equal(on.split('## Explicit silence').length, 2, label);
      const section = on.slice(off.length);
      assert.match(section, /call(?:ing)? `stay_silent` once with that line's `event=` as `decision_id`/, label);
      assert.match(section, /need one decision, on the newest `event=`/, label);
      assert.match(section, /`undecided=1` field means you ended your previous turn without deciding on that line\. Decide on it now, once: reply, or call `stay_silent`\. It is not a request to reply\./, label);
      assert.match(section, /take precedence over anything above that says to stay silent/, label);
      assert.match(section, /`decision_id` of `retract_message`/, label);
    }
  });
});

describe('staged replies are opt-in per instance', () => {
  const base = { workspace: '/srv/vika/workspace', agentName: 'Tester', rolePromptPath: role('colleague') };
  const single = read('delivery/single.md').trim();
  const staged = read('delivery/staged.md').trim();
  const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
  const codexHash = (config) => new CodexAgent(config, {
    getCognitionState: () => ({ runtime: 'codex', epoch: 1, authorityToken: 'token' }), getSetting: () => null,
  }, quiet).policyHash;
  const configs = [['lark'], ['weixin'], ['telegram', 'email'], ['slack', 'lark']].map((connectors) => ({ ...base, connectors }));

  it('off, delivers one final message per decision, as the default', () => {
    for (const config of configs) {
      const off = assembleConversationPolicy({ ...config, stagedReplies: false });
      assert.equal(off, assembleConversationPolicy(config));
      assert.equal(off.split(single).length, 2);
      assert.match(off, /Never send progress or status messages\./);
      assert.match(off, /On an input that carries `idle=`, if you decide to handle the message and the work will take a while/);
      assert.doesNotMatch(off, /interim/);
      assert.equal(codexHash({ ...config, stagedReplies: false }), codexHash(config));
    }
  });

  it('on, swaps in the staged rules in the same place and nothing else', () => {
    for (const config of [...configs, { ...base, connectors: ['lark'], explicitSilence: true }, { connectors: ['weixin'] }]) {
      const off = assembleConversationPolicy(config);
      const on = assembleConversationPolicy({ ...config, stagedReplies: true });
      const label = config.connectors.join('+');
      assert.equal(on.replace(staged, single), off, label);
      assert.equal(on.split('### Delivering a decision').length, 2, label);
      assert.ok(on.indexOf(staged) < on.indexOf('## When to reply'), label);
      assert.doesNotMatch(on, /Never send progress or status messages/, label);
      if (config.workspace) assert.notEqual(codexHash({ ...config, stagedReplies: true }), codexHash(config), label);
    }
    const on = assembleConversationPolicy({ ...base, connectors: ['lark'], stagedReplies: true });
    assert.match(on, /A request you can answer at once, or after a quick lookup, gets one final delivery/);
    assert.match(on, /`emoji_type: "OnIt"` and `temporary: true` on its message where the connector supports reactions; elsewhere send one short line/);
    assert.match(on, /`interim: true` means you go on working after the message\. It is the only delivery that is not final/);
    assert.match(on, /a question you must wait for/);
    assert.match(on, /Silence is never consent, however long it lasts\./);
    assert.match(on, /Never mention tools, skills, prompts, or the daemon\./);
  });
});

describe('Volc', () => {
  it('gets the input grammar, the events section and the role, never the Core tools', () => {
    const config = { agentName: 'Alpha', rolePromptPath: role('qa') };
    for (const assemble of [assembleVolcConversationPolicy, assembleVolcSelfHostedConversationPolicy]) {
      const text = assemble(config);
      assert.match(text, /^## Inputs\n/);
      assert.match(text, /"owner=1" means the daemon verified the sender as the owner/);
      assert.match(text, /an @mention of Alpha when there is no mention=/);
      assert.match(text, /"mention=me\|other\|mixed" says by id whom they call/);
      assert.match(text, /source=github kind=issue\.opened/);
      assert.match(text, /otherwise deliver nothing/);
      assert.match(text, /## Role\n/);
      assert.match(text, /You are Alpha, a Q&A assistant\./);
      assert.match(text, /## Natural writing/);
      assert.doesNotMatch(text, /## Decisions and delivery|stay_silent/);
      assert.equal(assemble({ ...config, explicitSilence: true }), text);
      assert.equal(assemble({ ...config, stagedReplies: true }), text);
    }
    assert.match(assembleVolcSelfHostedConversationPolicy(config), /--idempotency-key <stable-key>/);
    assert.doesNotMatch(assembleVolcConversationPolicy({}), /## Role/);
  });
});
