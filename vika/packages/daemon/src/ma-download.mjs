// Download an authenticated MA output to private disk. The signed storage URL
// is refreshed on every attempt; it is never persisted or given MA credentials.
import fs from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
const invalid = (message) => Object.assign(new Error(message), { code: 'invalid_content' });
const integer = (value) => /^\d+$/.test(String(value ?? '')) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
const strongETag = (value) => typeof value === 'string' && /^"[^"\r\n]+"$/.test(value) ? value : null;
async function digestFile(filename) {
  const hash = createHash('sha256');
  const handle = await fs.promises.open(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    if (!(await handle.stat()).isFile()) throw invalid('Download cache must be a regular file');
    for await (const chunk of handle.createReadStream({ autoClose: false })) hash.update(chunk);
    return hash.digest('hex');
  } finally { await handle.close(); }
}
async function saveState(filename, state) {
  const temporary = `${filename}.${randomUUID()}.tmp`;
  const handle = await fs.promises.open(temporary, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(state)); await handle.sync(); }
  finally { await handle.close(); }
  await fs.promises.rename(temporary, filename);
}
async function regularSize(filename) {
  try {
    const stat = await fs.promises.lstat(filename);
    if (!stat.isFile() || stat.isSymbolicLink()) throw invalid('Download cache must be a regular file');
    return stat.size;
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

export async function downloadMAFile({ destination, fileId, sessionId, detail, fetch = globalThis.fetch,
  attempts = 5, idleTimeoutMs = 30000, retryDelayMs = 250, onProgress = null }) {
  const partial = `${destination}.part`;
  const metadata = `${destination}.download.json`;
  let state = null;
  if (await regularSize(metadata) !== null) {
    try { state = JSON.parse(await fs.promises.readFile(metadata, 'utf8')); }
    catch { throw invalid('Download recovery metadata is invalid'); }
    if (state.fileId !== fileId || state.sessionId !== sessionId) throw invalid('Download recovery identity does not match');
  }
  const cachedSize = await regularSize(destination);
  if (cachedSize !== null) {
    if (!state?.complete || state.total !== cachedSize || !state.sha256
      || await digestFile(destination) !== state.sha256) throw invalid('Completed download cache failed integrity validation');
    return { bytes: cachedSize, sha256: state.sha256, resumed: false };
  }
  let lastError;
  let resumed = false;
  for (let attempt = 0; attempt < attempts; attempt++) {
    let handle; let controller; let timer;
    try {
      const file = await detail();
      if (file?.id !== fileId || file.scope?.type !== 'session' || file.scope?.id !== sessionId) {
        throw invalid('MA output file does not belong to this session');
      }
      const url = new URL(file.download_url);
      if (url.protocol !== 'https:') throw invalid('MA output detail must provide an HTTPS download URL');
      const declaredSize = integer(file.bytes ?? file.size);
      let offset = await regularSize(partial) ?? 0;
      const canResume = offset > 0 && strongETag(state?.etag) && Number.isSafeInteger(state?.total)
        && offset < state.total && (declaredSize === null || declaredSize === state.total);
      if (!canResume) offset = 0;
      controller = new AbortController();
      const resetIdle = () => {
        clearTimeout(timer);
        timer = setTimeout(() => controller.abort(new Error('MA download stalled')), idleTimeoutMs);
        timer.unref?.();
      };
      resetIdle();
      const response = await fetch(url, { headers: { 'Accept-Encoding': 'identity',
        ...(offset ? { Range: `bytes=${offset}-`, 'If-Range': state.etag } : {}) },
        redirect: 'error', signal: controller.signal });
      if (![200, 206].includes(response.status)) {
        if (response.status === 416) state = null;
        throw new Error(`MA storage download failed (${response.status})`);
      }
      if (response.headers.get('content-encoding') && response.headers.get('content-encoding') !== 'identity') {
        throw invalid('MA download must not apply content encoding to byte ranges');
      }
      const etag = strongETag(response.headers.get('etag'));
      const length = integer(response.headers.get('content-length'));
      let total; let expectedBytes;
      if (response.status === 206) {
        const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get('content-range') ?? '');
        if (!match) throw invalid('MA download returned an invalid Content-Range');
        const [start, end, size] = match.slice(1).map(integer);
        if ([start, end, size].includes(null) || start !== offset || end < start || end >= size
          || (offset && (etag !== state.etag || size !== state.total))) {
          // A validator mismatch must never append bytes from another object.
          state = null;
          throw invalid('MA download range or validator changed; partial bytes will not be combined');
        }
        total = size; expectedBytes = end - start + 1;
        if (length !== null && length !== expectedBytes) throw invalid('MA download range length is inconsistent');
        resumed ||= offset > 0;
      } else {
        // Servers may ignore Range (including an If-Range mismatch). Discard the
        // old partial only after accepting a complete response's metadata.
        offset = 0;
        total = length ?? declaredSize;
        expectedBytes = total;
        if (response.headers.has('content-range')) throw invalid('Unexpected Content-Range on a full download');
      }
      if (!Number.isSafeInteger(total) || total < 1 || (declaredSize !== null && declaredSize !== total)) {
        throw invalid('MA output size is missing or disagrees with storage');
      }
      state = { fileId, sessionId, total, etag, complete: false };
      await saveState(metadata, state);
      handle = await fs.promises.open(partial, fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
      if (!(await handle.stat()).isFile()) throw invalid('Partial download must be a regular file');
      if (!offset) await handle.truncate(0);
      let received = 0;
      for await (const chunk of response.body) {
        resetIdle();
        if (received + chunk.byteLength > expectedBytes) throw invalid('MA download exceeded its declared range length');
        let written = 0;
        while (written < chunk.byteLength) {
          const result = await handle.write(chunk, written, chunk.byteLength - written, offset + received + written);
          if (!result.bytesWritten) throw new Error('Partial download write made no progress');
          written += result.bytesWritten;
        }
        received += chunk.byteLength;
        onProgress?.({ bytes: offset + received, total, attempt, resumed });
      }
      await handle.sync(); await handle.close(); handle = null;
      if (received !== expectedBytes || offset + received !== total) throw new Error('MA download ended before the complete object arrived');
      const sha256 = await digestFile(partial);
      await saveState(metadata, { ...state, complete: true, sha256 });
      await fs.promises.rename(partial, destination);
      return { bytes: total, sha256, resumed };
    } catch (error) {
      lastError = error;
      // Metadata/range violations are fail-closed. Network interruption, expired
      // signed URL, or truncation may safely retry from a validated checkpoint.
      if (error.code === 'invalid_content') {
        if (state === null) await saveState(metadata, { fileId, sessionId, total: 0, etag: null, complete: false });
        throw error;
      }
    } finally {
      clearTimeout(timer); controller?.abort();
      if (handle) { try { await handle.sync(); } finally { await handle.close(); } }
    }
    if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, retryDelayMs * (attempt + 1)));
  }
  throw invalid(`MA download interrupted; partial file retained for a safe retry: ${lastError?.message ?? 'unknown transfer error'}`);
}
