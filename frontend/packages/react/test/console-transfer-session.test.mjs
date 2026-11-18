import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('Console converts browser files and OV CLI outputs to native transfers', async () => {
  const calls = [];
  const picked = [];
  const saved = [];
  const imported = [];
  const importedUri = 'viking://resources/qa/sample';
  const fixture = new File(['fixture'], 'sample.md');
  // A ZIP with a manifest, a resource and an archive comment.
  const validPack = Buffer.from(
    'UEsDBBQAAAAAAAAAIQBZX5KlJQAAACUAAAANAAAAbWFuaWZlc3QuanNvbnsicm9vdCI6eyJ1cmkiOiJ2aWtpbmc6Ly9yZXNvdXJjZXMifX1QSwMEFAAAAAAAAAAhAGxuK2APAAAADwAAAAkAAAByZWFkbWUubWRBIHRlc3QgcmVzb3VyY2VQSwECFAMUAAAAAAAAACEAWV+SpSUAAAAlAAAADQAAAAAAAAAAAAAAgAEAAAAAbWFuaWZlc3QuanNvblBLAQIUAxQAAAAAAAAAIQBsbitgDwAAAA8AAAAJAAAAAAAAAAAAAACAAVAAAAByZWFkbWUubWRQSwUGAAAAAAIAAgByAAAAhgAAABcAT3BlblZpa2luZyB0ZXN0IGZpeHR1cmU=',
    'base64',
  );
  let exportBytes = validPack;
  let selectedPack = new File([validPack], 'sample.ovpack');
  const host = await mount(
    async (url, init) => {
      const { pathname, search } = new URL(url);
      const body =
        typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
      calls.push({ pathname, search, body });
      if (pathname.endsWith('/temp_upload'))
        return ok({ temp_file_id: 'upload-1' });
      if (pathname.endsWith('/download')) return new Response('download bytes');
      if (pathname.endsWith('/export')) return new Response(exportBytes);
      if (pathname.endsWith('/pack/import')) return ok({ uri: importedUri });
      return ok({});
    },
    {
      commandOptions: {
        async choosePackFile(uri) {
          picked.push(uri);
          return selectedPack;
        },
        onPackImported(uri) {
          imported.push(uri);
          throw new Error('Host navigation failed');
        },
        files: {
          async pickFile() {
            return fixture;
          },
          async download(blob, name) {
            saved.push([Buffer.from(await blob.arrayBuffer()), name]);
          },
        },
      },
    },
  );
  try {
    await host.execute(
      'ov write viking://resources/test.md --from-file sample.md',
    );
    assert.deepEqual(calls.at(-1).body, {
      uri: 'viking://resources/test.md',
      content: 'fixture',
      mode: 'replace',
    });
    await host.execute('ov add-resource sample.md --to viking://resources/qa');
    assert.equal(calls.at(-2).body.get('file').name, 'sample.md');
    assert.deepEqual(calls.at(-1).body, {
      to: 'viking://resources/qa',
      temp_file_id: 'upload-1',
    });
    const beforeGit = calls.length;
    await host.execute('ov add-resource git@example.invalid:org/repo.git');
    assert.equal(calls.length, beforeGit + 1);
    assert.deepEqual(calls.at(-1).body, {
      path: 'git@example.invalid:org/repo.git',
    });
    await host.execute(
      'ov import sample.ovpack viking://resources/qa --on-conflict skip --vector-mode recompute',
    );
    assert.equal(picked.at(-1), 'viking://resources/qa');
    assert.deepEqual(calls.at(-1).body, {
      parent: 'viking://resources/qa',
      on_conflict: 'skip',
      vector_mode: 'recompute',
      temp_file_id: 'upload-1',
    });
    assert.deepEqual(imported, [importedUri]);
    assert.equal(host.records.at(-1).dataset.status, 'success');
    await host.execute('ov get viking://resources/test.md ./sample.md');
    assert.equal(
      new URLSearchParams(calls.at(-1).search).get('uri'),
      'viking://resources/test.md',
    );
    await host.execute(
      'ov export viking://resources/qa ./sample --include-vectors',
    );
    assert.deepEqual(calls.at(-1).body, {
      uri: 'viking://resources/qa',
      include_vectors: true,
    });
    assert.deepEqual(saved, [
      [Buffer.from('download bytes'), 'sample.md'],
      [validPack, 'sample.ovpack'],
    ]);

    const count = calls.length;
    selectedPack = new File(['mp4'], 'sample.mp4');
    await host.execute('ov import sample.ovpack viking://resources/qa');
    assert.match(host.records.at(-1).textContent, /valid .ovpack/i);
    for (const name of ['backup', 'restore']) {
      await host.execute(`ov ${name} sample.ovpack`);
      assert.match(host.records.at(-1).textContent, /unavailable/i);
    }
    assert.equal(calls.length, count);

    const endOffset = validPack.lastIndexOf(Buffer.from('PK\x05\x06'));
    const directoryOffset = validPack.readUInt32LE(endOffset + 16);
    const secondEntry = validPack.indexOf(
      Buffer.from('PK\x01\x02'),
      directoryOffset + 4,
    );
    const wrongDirectorySignature = Buffer.from(validPack);
    wrongDirectorySignature.writeUInt32LE(0, secondEntry);
    const wrongDirectoryCount = Buffer.from(validPack);
    wrongDirectoryCount.writeUInt16LE(1, endOffset + 8);
    wrongDirectoryCount.writeUInt16LE(1, endOffset + 10);
    for (const truncated of [
      validPack.subarray(0, 4),
      validPack.subarray(0, -1),
      Buffer.concat([
        validPack.subarray(0, directoryOffset),
        validPack.subarray(endOffset),
      ]),
      wrongDirectorySignature,
      wrongDirectoryCount,
    ]) {
      exportBytes = truncated;
      await host.execute('ov export viking://resources/qa ./sample');
      assert.equal(host.records.at(-1).dataset.status, 'error');
    }
    assert.equal(saved.length, 2);

    // The classic end record defers the directory size and offset to ZIP64.
    const zip64Pack = Buffer.from(
      'UEsDBC0AAAAAAAAAIQBZX5Kl//////////8NABQAbWFuaWZlc3QuanNvbgEAEAAlAAAAAAAAACUAAAAAAAAAeyJyb290Ijp7InVyaSI6InZpa2luZzovL3Jlc291cmNlcyJ9fVBLAQItAy0AAAAAAAAAIQBZX5Kl//////////8NABQAAAAAAAAAAACAAQAAAABtYW5pZmVzdC5qc29uAQAQACUAAAAAAAAAJQAAAAAAAABQSwYGLAAAAAAAAAAtAC0AAAAAAAAAAAABAAAAAAAAAAEAAAAAAAAATwAAAAAAAABkAAAAAAAAAFBLBgcAAAAAswAAAAAAAAABAAAAUEsFBgAAAAABAAEA//////////8AAA==',
      'base64',
    );
    exportBytes = zip64Pack;
    await host.execute('ov export viking://resources/qa ./large');
    assert.deepEqual(saved.at(-1), [zip64Pack, 'large.ovpack']);
  } finally {
    await host.dispose();
  }
});

test('Console uses the CLI session routes and argument mappings', async () => {
  const calls = [];
  const host = await mount(async (url, init) => {
    calls.push({
      method: init.method,
      pathname: new URL(url).pathname.replace('/gateway', ''),
      body: init.body && JSON.parse(init.body),
    });
    return ok({});
  });
  try {
    await host.execute(
      'ov session new --session-id thread-id --event-tags source=qa --no-auto-commit',
    );
    assert.deepEqual(calls.at(-1), {
      method: 'POST',
      pathname: '/api/v1/sessions',
      body: {
        session_id: 'thread-id',
        memory_extraction_config: { events: { tags: ['source=qa'] } },
        auto_commit_policy: null,
      },
    });
    await host.execute('ov session config set thread-id --no-event-tags');
    assert.deepEqual(calls.at(-1), {
      method: 'PATCH',
      pathname: '/api/v1/sessions/thread-id/config',
      body: { memory_extraction_config: { events: { tags: [] } } },
    });
    const count = calls.length;
    await host.execute('ov session config set thread-id');
    assert.match(host.records.at(-1).textContent, /--no-auto-commit/);
    assert.equal(host.records.at(-1).dataset.status, 'error');
    assert.equal(calls.length, count);
    await host.execute(
      `ov session add-messages thread-id '[{"role":"user","content":"test"}]'`,
    );
    assert.deepEqual(calls.at(-1), {
      method: 'POST',
      pathname: '/api/v1/sessions/thread-id/messages/batch',
      body: { messages: [{ role: 'user', content: 'test' }] },
    });
    await host.execute('ov session commit thread-id --no-event-tags');
    assert.deepEqual(calls.at(-1), {
      method: 'POST',
      pathname: '/api/v1/sessions/thread-id/commit',
      body: { extraction_metadata: { event: { tags: [] } } },
    });
  } finally {
    await host.dispose();
  }
});
