import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { downloadMAFile } from '../src/ma-download.mjs';

const bytes = Buffer.from('0123456789');
function setup(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ma-download-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  let details = 0;
  return { destination: path.join(directory, 'file.bin'), fileId: 'file-test', sessionId: 'session', retryDelayMs: 1,
    detail: async () => ({ id: 'file-test', bytes: bytes.length, scope: { type: 'session', id: 'session' },
      download_url: `https://storage.test/object?signature=${++details}` }) };
}
const whole = (body = bytes, extra = {}) => new Response(body, { headers: { etag: '"one"', 'content-length': String(bytes.length), ...extra } });
const tail = (start = 4, extra = {}) => new Response(bytes.subarray(start), { status: 206,
  headers: { etag: '"one"', 'content-length': String(bytes.length - start), 'content-range': `bytes ${start}-9/10`, ...extra } });
function interrupted(extra = {}) {
  // Fail on the next read, after the downloader has persisted the first chunk.
  // A timer can fire before filesystem setup finishes on a busy machine.
  let sent = false;
  const stream = new ReadableStream({ pull(controller) {
    if (sent) controller.error(new Error('connection reset'));
    else { sent = true; controller.enqueue(bytes.subarray(0, 4)); }
  } }, { highWaterMark: 0 });
  return whole(stream, extra);
}

test('refreshes expired signed URL and resumes interrupted bytes using a strong validator', async (t) => {
  const f = setup(t); const requests = [];
  const result = await downloadMAFile({ ...f, fetch: async (url, options) => {
    requests.push({ url: String(url), headers: options.headers });
    assert.equal(options.headers.Authorization, undefined);
    if (requests.length === 1) return interrupted();
    assert.equal(options.headers.Range, 'bytes=4-');
    assert.equal(options.headers['If-Range'], '"one"');
    if (requests.length === 2) return new Response('', { status: 403 });
    return tail();
  } });
  assert.equal(result.resumed, true); assert.equal(result.bytes, 10);
  assert.deepEqual(fs.readFileSync(f.destination), bytes);
  assert.equal(new Set(requests.map((request) => request.url)).size, 3);
  const persisted = fs.readFileSync(`${f.destination}.download.json`, 'utf8');
  assert.doesNotMatch(persisted, /signature|https/);
});

test('a replacement process resumes the durable partial without restarting the download', async (t) => {
  const f = setup(t);
  await assert.rejects(downloadMAFile({ ...f, attempts: 1, fetch: async () => interrupted() }), /partial file retained/);
  assert.equal(fs.statSync(`${f.destination}.part`).size, 4);
  await downloadMAFile({ ...f, fetch: async (_url, options) => { assert.equal(options.headers.Range, 'bytes=4-'); return tail(); } });
  assert.deepEqual(fs.readFileSync(f.destination), bytes);
});

test('a server ignoring Range restarts safely rather than appending a whole object', async (t) => {
  const f = setup(t); let calls = 0;
  await downloadMAFile({ ...f, fetch: async (_url, options) => {
    if (++calls === 1) return interrupted();
    assert.equal(options.headers.Range, 'bytes=4-'); return whole();
  } });
  assert.deepEqual(fs.readFileSync(f.destination), bytes);
});

test('weak or missing ETags never authorize a partial append', async (t) => {
  for (const etag of ['', 'W/"weak"']) {
    const f = { ...setup(t) }; let calls = 0;
    await downloadMAFile({ ...f, fetch: async (_url, options) => {
      if (++calls === 1) return interrupted({ etag });
      assert.equal(options.headers.Range, undefined); return whole();
    } });
    assert.deepEqual(fs.readFileSync(f.destination), bytes);
  }
});

test('wrong range, changed ETag, response length and cross-session detail fail closed', async (t) => {
  for (const headers of [{ 'content-range': 'bytes 5-9/10' }, { etag: '"changed"' }, { 'content-length': '99' }]) {
    const f = setup(t); let calls = 0;
    await assert.rejects(downloadMAFile({ ...f, fetch: async () => ++calls === 1 ? interrupted() : tail(4, headers) }), /range|validator|length/);
    assert.equal(fs.existsSync(f.destination), false);
  }
  const f = setup(t);
  await assert.rejects(downloadMAFile({ ...f, detail: async () => ({ id: 'file-test', scope: { type: 'session', id: 'other' } }),
    fetch: () => assert.fail('must not fetch foreign output') }), /does not belong/);
});

test('truncation resumes, completed cache validates bytes, and symlink partials are refused', async (t) => {
  const f = setup(t); let calls = 0;
  await downloadMAFile({ ...f, fetch: async () => ++calls === 1 ? whole(bytes.subarray(0, 4)) : tail() });
  await downloadMAFile({ ...f, fetch: () => assert.fail('completed cache should not be downloaded again') });
  fs.writeFileSync(f.destination, 'XXXXXXXXXX');
  await assert.rejects(downloadMAFile({ ...f }), /integrity/);
  const g = setup(t); const outside = `${g.destination}.outside`; fs.writeFileSync(outside, 'private');
  fs.symlinkSync(outside, `${g.destination}.part`);
  await assert.rejects(downloadMAFile({ ...g, fetch: async () => whole() }), /regular file/);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'private');
});

test('416 resets a stale partial and progressing downloads use an idle rather than total timeout', async (t) => {
  const f = setup(t); let calls = 0;
  await downloadMAFile({ ...f, fetch: async (_url, options) => {
    if (++calls === 1) return interrupted();
    if (calls === 2) return new Response('', { status: 416 });
    assert.equal(options.headers.Range, undefined); return whole();
  } });
  assert.deepEqual(fs.readFileSync(f.destination), bytes);
  const g = setup(t); let offset = 0; let finished = false;
  await downloadMAFile({ ...g, idleTimeoutMs: 100, fetch: async (_url, { signal }) => {
    const body = new ReadableStream({
      start(controller) { signal.addEventListener('abort', () => { if (!finished) controller.error(new Error('idle timeout')); }); },
      async pull(controller) {
        await delay(40);
        controller.enqueue(bytes.subarray(offset, offset + 2)); offset += 2;
        if (offset === bytes.length) { finished = true; controller.close(); }
      },
    });
    return whole(body);
  } });
  assert.deepEqual(fs.readFileSync(g.destination), bytes);
});
