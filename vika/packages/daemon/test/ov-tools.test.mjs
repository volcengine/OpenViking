// Copyright © 2026 Volcano Engine (火山引擎) and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, it } from 'node:test';
import { OV_DEFAULT_PARENT, OVToolClient, reservedTarget } from '../src/ov-tools.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

// The instance's ovConfig files, one per client.
const CONFIGS = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-tools-'));
after(() => fs.rmSync(CONFIGS, { recursive: true, force: true }));
let configs = 0;
function configFile(fields) {
  const file = path.join(CONFIGS, `ov-${++configs}.json`);
  fs.writeFileSync(file, typeof fields === 'string' ? fields : JSON.stringify(fields));
  return file;
}

// The client against the scripted server, as the instance's own OV user, with
// a small timeout budget and an injectable fetch.
function client(ov, options = {}, config = {}) {
  return new OVToolClient({
    configPath: configFile({ url: ov.url, api_key: 'test-key', ...config }),
    timeoutMs: 2_000, readTimeoutMs: 1_000, ...options,
  });
}

function requests(ov, route) {
  return ov.state.requests.filter((request) => typeof route === 'string' ? request.route === route : route.test(request.route));
}

// A fetch that never answers, like a server that went silent: it settles only
// through the abort signal the client passes, the way a real fetch would.
function hangingFetch() {
  return (input, init) => new Promise((resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(init.signal.reason));
  });
}

describe('OpenViking tool client', () => {
  it('speaks as the instance from its ovConfig with no peer, and refuses a missing or broken config', async (t) => {
    const ov = await fakeOV(t);
    // A config from before peers were dropped still names one: never sent.
    const instance = client(ov, {}, { api_key: 'file-key', account: 'acct', user: 'someone', agent: 'bot', actor_peer_id: 'instance-a' });
    await instance.find({ query: 'hello' });
    await instance.list({ uri: 'viking://' });
    for (const request of ov.state.requests) {
      assert.equal(request.headers['x-api-key'], 'file-key');
      assert.equal(request.headers['x-openviking-account'], 'acct');
      assert.equal(request.headers['x-openviking-user'], 'someone');
      assert.equal(request.headers['x-openviking-agent'], 'bot');
      assert.equal(request.headers['x-openviking-actor-peer'], undefined);
    }

    const unconfigured = new OVToolClient({ configPath: '' });
    await assert.rejects(() => unconfigured.find({ query: 'x' }),
      (error) => error.code === 'openviking_unavailable' && /not configured/.test(error.message));
    const later = path.join(CONFIGS, 'later.json');
    const missing = new OVToolClient({ configPath: later });
    await assert.rejects(() => missing.find({ query: 'x' }),
      (error) => error.code === 'openviking_unavailable' && /missing or invalid/.test(error.message));
    // Fixed later, the same client connects: a failure is never kept.
    fs.writeFileSync(later, JSON.stringify({ url: ov.url, api_key: 'test-key' }));
    assert.equal((await missing.find({ query: 'x' })).text, 'No matching context found.');
    await assert.rejects(() => new OVToolClient({ configPath: configFile('{ nope') }).find({ query: 'x' }),
      (error) => error.code === 'openviking_unavailable');
    await assert.rejects(() => new OVToolClient({ configPath: configFile({ url: 'ftp://nope' }) }).find({ query: 'x' }),
      (error) => error.code === 'openviking_unavailable');
  });

  it('finds and searches with one ranked, deduplicated answer', async (t) => {
    const ov = await fakeOV(t);
    ov.state.searchResult = {
      memories: [{ uri: 'viking://user/u/memories/a.md', score: 0.8123, abstract: 'the first fact' },
        { uri: 'viking://user/u/memories/preferences/李四/保密.md', score: 0.7, abstract: '不要透露项目进度' }],
      resources: [{ uri: 'viking://resources/doc.md', score: 0.6, abstract: 'a doc' }],
      skills: [
        { uri: 'viking://user/u/skills/pkg/.abstract.md', score: 0.4, abstract: 'pkg summary' },
        { uri: 'viking://user/u/skills/pkg/SKILL.md', score: 0.75, abstract: 'the skill itself' },
      ],
      total: 5,
    };
    const found = await client(ov).find({ query: 'what do I know', limit: 20, target_uri: 'viking://resources', context_type: 'resource', min_score: 0.3 });
    const request = requests(ov, 'POST /api/v1/search/find').at(-1);
    assert.deepEqual(request.body, {
      query: 'what do I know', limit: 20, target_uri: 'viking://resources', context_type: 'resource', score_threshold: 0.3,
    });
    assert.match(found.text, /\[memory 81%\] viking:\/\/user\/u\/memories\/a\.md/);
    // A person's preference says whose it is.
    assert.match(found.text, /\[memory, preferences of 李四 70%\] viking:\/\/user\/u\/memories\/preferences\/李四\/保密\.md/);
    assert.equal(found.data.hits.find((hit) => hit.person).person, '李四');
    // Both skill files collapse onto the package's SKILL.md, keeping the better hit.
    assert.equal(found.data.hits.filter((hit) => hit.uri.endsWith('SKILL.md')).length, 1);
    assert.equal(found.data.hits.find((hit) => hit.type === 'skill').score, 0.75);
    assert.match(found.text, /Use openviking_read to expand a URI/);

    await client(ov).search({ query: 'exact words', search_type: 'keywords' });
    assert.deepEqual(requests(ov, 'POST /api/v1/search/search').at(-1).body, {
      query: 'exact words', limit: 10, search_type: 'keywords',
    });

    ov.state.searchResult = { memories: [], resources: [], skills: [], total: 0 };
    assert.equal((await client(ov).find({ query: 'nothing' })).text, 'No matching context found.');
  });

  it('truncates long abstracts and caps the whole find text', async (t) => {
    const ov = await fakeOV(t);
    ov.state.searchResult = {
      memories: [{ uri: 'viking://user/u/memories/long.md', score: 0.9, abstract: 'x'.repeat(600) }],
      resources: [], skills: [], total: 1,
    };
    const found = await client(ov).find({ query: 'long' });
    assert.equal(found.data.hits[0].abstract.length, 500);
    assert.ok(found.text.length < found.data.hits[0].abstract.length + 200);

    ov.state.searchResult = {
      memories: Array.from({ length: 60 }, (_, index) => ({
        uri: `viking://user/u/memories/${index}.md`, score: 0.9, abstract: 'y'.repeat(500),
      })),
      resources: [], skills: [], total: 60,
    };
    const capped = await client(ov).find({ query: 'many', limit: 60 });
    assert.ok(capped.text.length <= 20_000);
    assert.match(capped.text, /Result truncated/);
  });

  it('pages read by line, marks the end, and caps a page at 16k characters', async (t) => {
    const ov = await fakeOV(t);
    ov.state.files.set('viking://user/u/notes.md', 'one\ntwo\nthree\nfour\nfive');
    const first = await client(ov).read({ uri: 'viking://user/u/notes.md', limit: 2 });
    assert.equal(requests(ov, 'GET /api/v1/content/read').at(-1).query.limit, '3');
    assert.match(first.text, /lines 0-1, continue with offset=2/);
    assert.equal(first.data.next_offset, 2);
    assert.ok(first.text.includes('one\ntwo'));
    const second = await client(ov).read({ uri: 'viking://user/u/notes.md', offset: 2, limit: 2 });
    assert.equal(second.data.next_offset, 4);
    const last = await client(ov).read({ uri: 'viking://user/u/notes.md', offset: 4, limit: 2 });
    assert.equal(last.data.next_offset, null);
    assert.match(last.text, /whole remaining text/);
    assert.ok(last.text.includes('five'));

    ov.state.files.set('viking://user/u/huge.md', `${'a'.repeat(10_000)}\n${'b'.repeat(10_000)}\n${'c'.repeat(10_000)}`);
    const page = await client(ov).read({ uri: 'viking://user/u/huge.md', limit: 500 });
    assert.equal(page.data.next_offset, 1);
    assert.equal(page.data.lines, 1);
    assert.ok(!page.text.includes('b'.repeat(100)));

    ov.state.files.set('viking://user/u/single.md', 'z'.repeat(20_000));
    const single = await client(ov).read({ uri: 'viking://user/u/single.md' });
    assert.equal(single.data.next_offset, null);
    assert.ok(single.text.length < 20_100);
    assert.ok(single.text.includes('z'.repeat(100)));

    // A trailing newline is one more empty line to the server (verified against
    // a real one): the walk still ends exactly at the file's end.
    ov.state.files.set('viking://user/u/trailing.md', 'a\nb\n');
    const firstTrailing = await client(ov).read({ uri: 'viking://user/u/trailing.md', limit: 2 });
    assert.deepEqual([firstTrailing.data.lines, firstTrailing.data.next_offset], [2, 2]);
    assert.ok(firstTrailing.text.includes('a\nb'));
    const tailTrailing = await client(ov).read({ uri: 'viking://user/u/trailing.md', offset: 2, limit: 2 });
    assert.deepEqual([tailTrailing.data.lines, tailTrailing.data.next_offset], [1, null]);
  });

  it('lists directory entries and reports has_more', async (t) => {
    const ov = await fakeOV(t);
    ov.state.listings.set('viking://user/owner/resources', {
      entries: [
        { name: 'inbox', isDir: true, uri: 'viking://user/u/resources/inbox' },
        { name: 'doc.md', isDir: false, size: 120, uri: 'viking://user/u/resources/doc.md' },
      ],
      has_more: true,
    });
    const paged = await client(ov).list({ uri: 'viking://~/resources', limit: 1, offset: 1, recursive: true });
    const request = requests(ov, 'GET /api/v1/fs/ls').at(-1);
    assert.equal(request.query.node_limit, '1');
    assert.equal(request.query.offset, '1');
    assert.equal(request.query.recursive, 'true');
    // The page holds only the second entry; the marker still says there is more.
    assert.match(paged.text, /- \[file\] doc\.md \| 120 B \| viking:\/\/user\/u\/resources\/doc\.md/);
    assert.ok(!paged.text.includes('inbox'));
    assert.equal(paged.data.has_more, true);
    assert.match(paged.text, /More entries exist/);

    const listed = await client(ov).list({ uri: 'viking://~/resources' });
    assert.match(listed.text, /- \[dir\] inbox \| viking:\/\/user\/u\/resources\/inbox/);
    assert.equal(listed.data.entries.length, 2);

    const empty = await client(ov).list({ uri: 'viking://nowhere' });
    assert.equal(empty.text, '(no entries under viking://nowhere)');
  });

  it('adds a URL resource to the private inbox by default', async (t) => {
    const ov = await fakeOV(t);
    const added = await client(ov).addResource({ url: 'https://example.com/post', description: 'worth keeping' });
    const request = requests(ov, 'POST /api/v1/resources').at(-1);
    assert.deepEqual(request.body, {
      path: 'https://example.com/post', parent: OV_DEFAULT_PARENT, reason: 'worth keeping', wait: false, create_parent: true,
    });
    assert.equal(added.data.task_id, 'task-1');
    // The server answers with the canonical form of the private home alias.
    assert.equal(added.data.root_uri, 'viking://user/owner/resources/inbox/post');
    assert.match(added.text, /processing in the background/);

    const placed = await client(ov).addResource({ url: 'https://example.com/x', to: 'viking://resources/wiki/x' });
    assert.equal(requests(ov, 'POST /api/v1/resources').at(-1).body.to, 'viking://resources/wiki/x');
    assert.equal(placed.data.target, 'viking://resources/wiki/x');
  });

  it('adds a workspace file through temp_upload with its own name', async (t) => {
    const ov = await fakeOV(t);
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-file-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    fs.writeFileSync(path.join(directory, 'report.md'), '# Report\n\nbody');
    const added = await client(ov).addResource({
      file_path: 'report.md', workspace: directory, parent: 'viking://~/resources/reports', description: 'monthly',
    });
    const upload = requests(ov, 'POST /api/v1/resources/temp_upload').at(-1);
    assert.equal(upload.body, null); // multipart, not json
    assert.match(String(upload.headers['content-type']), /multipart\/form-data/);
    const resource = requests(ov, 'POST /api/v1/resources').at(-1);
    assert.match(resource.body.temp_file_id, /^upload_/);
    assert.equal(resource.body.source_name, 'report.md');
    assert.equal(resource.body.parent, 'viking://~/resources/reports');
    assert.equal(resource.body.reason, 'monthly');
    assert.equal(resource.body.wait, false);
    assert.equal(ov.state.uploads.get(resource.body.temp_file_id).name, 'report.md');
    assert.match(added.text, /report\.md|Resource accepted/);
    assert.equal(added.data.root_uri, 'viking://user/owner/resources/reports/report.md');
  });

  it('rejects add_resource input that is not exactly one URL or one workspace file', async (t) => {
    const ov = await fakeOV(t);
    const one = client(ov);
    await assert.rejects(() => one.addResource({}), (error) => error.code === 'invalid_input' && /exactly one of url or file_path/.test(error.message));
    await assert.rejects(() => one.addResource({ url: 'https://a.example', file_path: 'a.md' }), /exactly one of url or file_path/);
    await assert.rejects(() => one.addResource({ url: 'https://a.example', to: 'viking://t/a', parent: 'viking://p' }), /at most one of to or parent/);
    // What the daemon keeps is out of reach, in either spelling of the own root.
    for (const target of ['viking://~/resources/vika/asd/timers-state.json', 'viking://~/resources/emails', 'viking://user/owner/resources/meetings/1/note.md',
      'viking://~/memories/preferences/x', 'viking://user/owner/sessions/s', 'viking://~/peers/p']) {
      await assert.rejects(() => one.addResource({ url: 'https://a.example', to: target }),
        (error) => error.code === 'openviking_reserved_target');
      await assert.rejects(() => one.addResource({ url: 'https://a.example', parent: target }),
        (error) => error.code === 'openviking_reserved_target');
    }
    assert.equal(reservedTarget('viking://~/resources/emailsandmore'), null);
    assert.equal(reservedTarget('viking://resources/emails'), null, 'the shared root is not the daemon\'s');
    await assert.rejects(() => one.addResource({ url: 'ftp://example.com/f' }), /must be an http\(s\) address/);
    await assert.rejects(() => one.addResource({ url: `https://example.com/${'a'.repeat(5000)}` }), /must be an http\(s\) address/);

    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-reject-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    fs.writeFileSync(path.join(directory, 'ok.md'), 'fine');
    const bounded = client(ov, { uploadMaxBytes: 3 });
    await assert.rejects(() => bounded.addResource({ file_path: 'ok.md', workspace: directory }), /at most .* MiB/);
    await assert.rejects(() => one.addResource({ file_path: '../escape.md', workspace: directory }), /file_path/);
    await assert.rejects(() => one.addResource({ file_path: '/abs/escape.md', workspace: directory }), /file_path/);
    assert.equal(requests(ov, 'POST /api/v1/resources').length, 0);
  });

  it('remembers one message on an independent session with the sync\'s memory types, committed once', async (t) => {
    const ov = await fakeOV(t);
    const line = '[chat=oc-1 source=lark msg=om-1 time=2026-11-07T09:00Z from=李四] 以后回复我都加个 emoji';
    const remembered = await client(ov).remember({ line, note: '[from=me] 李四希望回复他时带 emoji' });
    const created = requests(ov, 'POST /api/v1/sessions').at(-1);
    assert.match(created.body.session_id, /^vika-remember-/);
    assert.deepEqual(created.body.auto_commit_policy, {
      pending_token_threshold: 0, message_count_threshold: 0, idle_timeout_seconds: 0,
    });
    assert.deepEqual(created.body.memory_policy, { memory_types: ['profile', 'preferences', 'entities', 'events'] });
    assert.deepEqual(requests(ov, /messages/).map((request) => request.body), [
      { role: 'user', content: line },
      { role: 'assistant', content: '[from=me] 李四希望回复他时带 emoji' },
    ]);
    const session = ov.state.sessions.get(created.body.session_id);
    assert.equal(session.commits, 1);
    assert.match(remembered.data.task_id, /^commit-/);
    assert.equal(remembered.data.session_id, created.body.session_id);
    assert.match(remembered.text, /under their name/);

    await client(ov).remember({ line });
    assert.deepEqual(requests(ov, /messages/).at(-1).body, { role: 'user', content: line }, 'no note, no assistant line');
  });

  it('does not retry a remember commit that timed out', async (t) => {
    const ov = await fakeOV(t);
    const calls = [];
    const through = async (input, init) => {
      calls.push(String(input));
      if (String(input).includes('/commit')) return hangingFetch()(input, init);
      return fetch(input, init);
    };
    const slow = client(ov, { fetch: through, timeoutMs: 80 });
    await assert.rejects(() => slow.remember({ line: 'might land anyway' }), (error) =>
      error.code === 'openviking_unavailable' && /may already have landed/.test(error.message));
    assert.equal(calls.filter((url) => url.includes('/commit')).length, 1);
    const sessions = [...ov.state.sessions.values()];
    assert.equal(sessions.length, 1);
    assert.deepEqual(sessions[0].messages, [{ role: 'user', content: 'might land anyway' }]);
    assert.equal(sessions[0].commits, 0);
  });

  it('maps every failure to one code without leaking the URL, the key or the body', async (t) => {
    const ov = await fakeOV(t);
    const codes = [[400, 'openviking_invalid_input'], [401, 'openviking_unauthorized'], [403, 'openviking_forbidden'],
      [408, 'openviking_unavailable'], [429, 'openviking_unavailable'], [500, 'openviking_unavailable'], [503, 'openviking_unavailable']];
    for (const [status, code] of codes) {
      ov.state.errors.set('POST /api/v1/search/find', status);
      await assert.rejects(() => client(ov).find({ query: 'x' }), (error) => {
        assert.equal(error.code, code);
        assert.ok(!error.message.includes(ov.url), 'message must not contain the URL');
        assert.ok(!error.message.includes('test-key'), 'message must not contain the key');
        assert.ok(!error.message.includes('injected'), 'message must not contain the body');
        return true;
      });
    }
    ov.state.errors.set('GET /api/v1/content/read', 404);
    await assert.rejects(() => client(ov).read({ uri: 'viking://user/u/gone.md' }),
      (error) => error.code === 'openviking_not_found');

    const offline = new OVToolClient({ configPath: configFile({ url: 'http://127.0.0.1:1', api_key: 'test-key' }) });
    await assert.rejects(() => offline.find({ query: 'x' }), (error) => {
      assert.equal(error.code, 'openviking_unavailable');
      assert.ok(!error.message.includes('127.0.0.1'));
      return true;
    });

    const hanging = client(ov, { fetch: hangingFetch(), timeoutMs: 60 });
    await assert.rejects(() => hanging.find({ query: 'x' }), (error) => {
      assert.equal(error.code, 'openviking_unavailable');
      assert.match(error.message, /timed out/);
      return true;
    });
  });
});
