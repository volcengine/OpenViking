import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('find and search pass Volc retrieval options to the native data plane', async () => {
  const calls = [];
  const rendered = 'Relevant context from viking://resources/wiki/example.md';
  const host = await mount(async (url, init) => {
    const path = new URL(url).pathname;
    const body = JSON.parse(init.body);
    calls.push({ path, body });
    return ok(body.mode === 'context' ? { entries: [], rendered } : {});
  });
  try {
    await host.execute(
      "ov find how does -n 12 retrieval work? -u viking://resources,viking://agent/skills -t .75 -L '[0, 2]' --after 2026-01-01 --before 2026-02-01 --events-time-decay-protection 0",
    );
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/find',
      body: {
        query: 'how does retrieval work?',
        target_uri: ['viking://resources', 'viking://agent/skills'],
        limit: 12,
        score_threshold: 0.75,
        level: [0, 2],
        since: '2026-01-01',
        until: '2026-02-01',
        events_time_decay_protection: '0',
      },
    });

    await host.execute('ov find legacy --target_uri viking:// --limit 7');
    assert.deepEqual(calls.at(-1).body, {
      query: 'legacy',
      target_uri: 'viking://',
      limit: 7,
    });

    await host.execute(
      'ov search --image "data:image/png;base64,YQ==" --mode context --session-id integration-test --events-time-decay-protection 7d',
    );
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/search',
      body: {
        image_url: 'data:image/png;base64,YQ==',
        mode: 'context',
        session_id: 'integration-test',
        events_time_decay_protection: '7d',
      },
    });
    assert.ok(host.records.at(-1).textContent.includes(rendered));

    await host.execute('ov find --tags external_id=123');
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/find',
      body: { tags: ['external_id=123'] },
    });
    await host.execute('ov search notes --context-type resource');
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/search',
      body: { query: 'notes', context_type: ['resource'] },
    });

    for (const input of [
      'ov find',
      'ov find notes --threshold foo',
      'ov find notes --limit 0',
      'ov find notes -L 0,text',
      'ov find notes -L 0,3',
      'ov find notes --events-time-decay-protection 5w',
      'ov search notes --mode invalid',
      'ov search notes --node-limit 0',
      'ov search --context-type resource',
      'ov search --mode list --tags external_id=123',
      'ov search notes --mode context -u viking://resources',
      'ov search notes --mode context -L 2',
    ]) {
      await host.execute(input);
      assert.equal(host.records.at(-1).dataset.status, 'error', input);
    }
    assert.equal(calls.length, 5);
  } finally {
    await host.dispose();
  }
});

test('find and ranked search display requested content and fall back to the abstract', async () => {
  const bodies = [];
  const host = await mount(async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return ok({
      resources: [
        {
          uri: 'viking://resources/notes.md',
          abstract: 'Notes abstract',
          content: 'First line\nSecond line',
        },
        { uri: 'viking://resources/other.md', abstract: 'Other abstract' },
        {
          uri: 'viking://resources/empty.md',
          abstract: 'Empty file abstract',
          content: '',
        },
        {
          uri: 'viking://resources/blank.md',
          abstract: 'Blank file abstract',
          content: ' \n\t ',
        },
      ],
    });
  });
  try {
    for (const command of [
      'ov find notes --read-content',
      'ov search notes --mode list --read-content',
    ]) {
      await host.execute(command);
      assert.equal(bodies.at(-1).read_content, true);
      const record = host.records.at(-1);
      assert.equal(
        record.querySelector('.ov-command-console__search-content').textContent,
        'First line\nSecond line',
      );
      const previews = [
        ...record.querySelectorAll('.ov-command-console__search-preview'),
      ];
      assert.deepEqual(
        previews.map((preview) => preview.textContent),
        ['Other abstract', 'Empty file abstract', 'Blank file abstract'],
      );
      assert.doesNotMatch(record.textContent, /Notes abstract/);
    }
    await host.execute('ov find notes');
    assert.match(host.records.at(-1).textContent, /Notes abstract/);
    assert.doesNotMatch(host.records.at(-1).textContent, /Second line/);
  } finally {
    await host.dispose();
  }
});

test('grep and glob pass their flags and defaults to the native data plane', async () => {
  const calls = [];
  const host = await mount(async (url, init) => {
    calls.push({ path: new URL(url).pathname, body: JSON.parse(init.body) });
    return ok({ matches: [] });
  });
  try {
    await host.execute(
      'ov grep "read me" -u viking://resources -i -n 32 -x viking://resources/archive -L 2',
    );
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/grep',
      body: {
        pattern: 'read me',
        uri: 'viking://resources',
        case_insensitive: true,
        node_limit: 32,
        exclude_uri: 'viking://resources/archive',
        level_limit: 2,
      },
    });

    await host.execute('ov grep readme --case_insensitive --node_limit 16');
    assert.deepEqual(calls.at(-1).body, {
      pattern: 'readme',
      uri: 'viking://',
      case_insensitive: true,
      node_limit: 16,
      level_limit: 10,
    });

    await host.execute('ov glob *.md');
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/search/glob',
      body: { pattern: '*.md', uri: 'viking://', node_limit: 256 },
    });

    await host.execute(
      'ov grep readme -u viking://resources --limit 12 --tags team=qa,kind=docs --fields tags --before-context 2 -a 3',
    );
    assert.deepEqual(calls.at(-1).body, {
      pattern: 'readme',
      uri: 'viking://resources',
      case_insensitive: false,
      node_limit: 12,
      level_limit: 10,
      tags: ['team=qa', 'kind=docs'],
      include_tags: true,
      before_context: 2,
      after_context: 3,
    });
    await host.execute('ov glob *.md --limit 8 --tags team=qa -f size,tags');
    assert.deepEqual(calls.at(-1).body, {
      pattern: '*.md',
      uri: 'viking://',
      node_limit: 8,
      tags: ['team=qa'],
      include_tags: true,
      extra_fields: ['size', 'tags'],
    });
    const count = calls.length;
    await host.execute('ov grep readme --after-context -1');
    assert.equal(host.records.at(-1).dataset.status, 'error');
    assert.equal(calls.length, count);
  } finally {
    await host.dispose();
  }
});
