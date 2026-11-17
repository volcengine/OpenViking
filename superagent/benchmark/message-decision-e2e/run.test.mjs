import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

test('smoke run exports tool, outbox, ledger and assessment evidence', () => {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'message-decision-e2e-'));
  try {
    const run = spawnSync(process.execPath, [
      'benchmark/message-decision-e2e/run.mjs',
      '--smoke',
      '--scenario', 'combined',
      '--out', output,
    ], { cwd: path.resolve(import.meta.dirname, '../..'), encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr || run.stdout);

    const summary = JSON.parse(fs.readFileSync(path.join(output, 'summary.json'), 'utf8'));
    assert.equal(summary.passed, true);
    assert.equal(summary.results.length, 1);
    const result = JSON.parse(fs.readFileSync(summary.results[0].resultPath, 'utf8'));
    assert.equal(result.assessment.passed, true);
    assert.deepEqual(result.toolCalls.at(-1).input.covers,
      ['evt-om_e2e_combined_a', 'evt-om_e2e_combined_b']);
    assert.equal(result.deliveries.length, 1);
    assert.deepEqual(result.decisions.map(({ event_id, outcome, covered_by }) =>
      [event_id, outcome, covered_by]), [
      ['evt-om_e2e_combined_a', 'delivered', 'evt-om_e2e_combined_b'],
      ['evt-om_e2e_combined_b', 'delivered', null],
    ]);
    assert.ok(Array.isArray(result.activities));
  } finally {
    fs.rmSync(output, { recursive: true, force: true });
  }
});
