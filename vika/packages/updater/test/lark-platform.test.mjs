import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { installLarkCliBinary } from '../../../scripts/release/lark-cli.mjs';

test('shared Lark installer selects checksummed Windows ZIP and retains Unix tar behavior', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-lark-assets-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bytes = Buffer.from('fixture archive');
  const hash = createHash('sha256').update(bytes).digest('hex');
  for (const [id, suffix, binary, flag] of [
    ['win32-x64', 'windows-amd64.zip', 'lark-cli.exe', '-xf'],
    ['linux-x64', 'linux-amd64.tar.gz', 'lark-cli', '-xzf'],
  ]) {
    fs.writeFileSync(path.join(root, 'package.json'), '{"version":"1.0.94"}');
    fs.writeFileSync(path.join(root, 'checksums.txt'), `${hash}  lark-cli-1.0.94-${suffix}\n`);
    const result = await installLarkCliBinary({ packageDir: root, platformId: id,
      fetchImpl: async url => { assert.ok(url.endsWith(suffix)); return new Response(bytes); },
      execFileSyncImpl(command, args) {
        assert.equal(command, 'tar'); assert.equal(args[0], flag); assert.equal(args[4], binary);
        fs.writeFileSync(path.join(args[3], binary), 'binary');
      },
    });
    assert.equal(path.basename(result.bin), binary);
    assert.equal(fs.readFileSync(result.bin, 'utf8'), 'binary');
  }
});
