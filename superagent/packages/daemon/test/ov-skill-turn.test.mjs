import assert from 'node:assert/strict';
import { test } from 'node:test';
import { withTurnSkills } from '../src/runtime-input.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { ClaudeAgent } from '../src/claude.mjs';
import { PiAgent } from '../src/pi.mjs';
import { DurableAgent } from '../src/durable.mjs';
import { OpencodeAgent } from '../src/opencode.mjs';
import { VolcAgent } from '../src/volc.mjs';
import dshTurn from '../src/dsh/turn.mjs';

for (const [name, bodyFor] of [
  ['claude', ClaudeAgent.prototype._bodyFor],
  ['pi', PiAgent.prototype._bodyFor],
  ['durable', DurableAgent.prototype._bodyFor],
  ['opencode', OpencodeAgent.prototype._bodyFor],
  ['volc', VolcAgent.prototype._bodyFor],
  ['dsh', dshTurn._bodyFor],
]) {
  test(`${name} prompts keep one Skill view across a batch and steering`, () => {
    const agent = {
      store: {}, needsHistoryPrefix: false, deliveredThroughSeq: 0,
      turnActive: false, turn: null,
    };
    const first = '<available_skills>\n- first\n</available_skills>';
    const later = '<available_skills>\n- later\n</available_skills>';
    const prepared = bodyFor.call(agent, [{ text: `one\n${first}` }, { text: `two\n${later}` }]);
    assert.equal(prepared.body, `one\ntwo\n${first}`);
    agent.turnActive = true;
    agent.turn = {};
    assert.equal(bodyFor.call(agent, [{ text: `three\n${later}` }]).body, 'three');
    agent.turnActive = false;
    agent.turn = null;
    assert.equal(bodyFor.call(agent, [{ text: `next\n${later}` }]).body, `next\n${later}`);
  });
}

test('one Skill view survives batching and same-turn steering, then resets next turn', () => {
  const first = '<available_skills>\n- first\n</available_skills>';
  const later = '<available_skills>\n- later\n</available_skills>';
  const items = [{ text: `input one\n${first}` }, { text: `input two\n${later}` }];
  assert.equal(withTurnSkills(items), `input one\ninput two\n${first}`);
  assert.equal(withTurnSkills([{ text: `input three\n${later}` }], { active: true }), 'input three');
  assert.equal(withTurnSkills([{ text: `next turn\n${later}` }]), `next turn\n${later}`);
  assert.equal(items[0].text, `input one\n${first}`);
});

test('a turn that starts without Skills does not acquire a different view when steered', () => {
  assert.equal(withTurnSkills([{ text: 'first' }]), 'first');
  assert.equal(withTurnSkills([{
    text: 'later\n<available_skills>\n- installed mid-turn\n</available_skills>',
  }], { active: true }), 'later');
});

test('oversized chunked downloads stop reading at the byte limit', async () => {
  let reads = 0;
  let cancelled = false;
  const stream = new ReadableStream({
    pull(controller) {
      reads += 1;
      if (reads <= 10) controller.enqueue(new Uint8Array(8));
      else controller.close();
    },
    cancel() { cancelled = true; },
  }, { highWaterMark: 0 });
  const connection = new OVConnection({ url: 'http://ov.test' }, {
    fetch: async () => new Response(stream),
  });
  await assert.rejects(() => connection.bytes('/download', { maxBytes: 10 }));
  assert.equal(cancelled, true);
  assert.equal(reads, 2);
});
