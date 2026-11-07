import test from 'node:test';
import assert from 'node:assert/strict';
import { connectorCrashDetail } from '../src/connectors/process.mjs';
test('connector crash detail omits routine structured logs while preserving errors', () => {
  assert.equal(connectorCrashDetail('{"level":"info","message":"meeting_notes_started"}\n'), '');
  const output = connectorCrashDetail('Error: broken pipe\n{"level":"debug","message":"heartbeat"}\n{"level":"error","message":"failure"}');
  assert.match(output, /broken pipe/);
  assert.match(output, /failure/);
  assert.doesNotMatch(output, /heartbeat/);
});
