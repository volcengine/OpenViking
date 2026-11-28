import test from 'node:test';
import assert from 'node:assert/strict';
import { assessScenario } from './scorer.mjs';

const events = {
  older: 'evt-om_e2e_older',
  newest: 'evt-om_e2e_newest',
};

test('accepts a newest fence that settles only the explicitly covered older input', () => {
  const result = assessScenario({
    id: 'older-only',
    expectedTool: {
      decision_id: events.newest,
      response_to_id: events.older,
      covers: [events.older],
    },
    expectedDecisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
    ],
    undecided: [events.newest],
  }, {
    toolCalls: [{
      tool: 'reply_message',
      input: {
        decision_id: events.newest,
        response_to_id: events.older,
        covers: [events.older],
      },
    }],
    decisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
    ],
  });

  assert.equal(result.passed, true);
  assert.deepEqual(result.checks.map(({ passed }) => passed), [true, true, true]);
});

test('rejects implicit settlement of the newest fence when only the older input was answered', () => {
  const result = assessScenario({
    id: 'older-only',
    expectedTool: {
      decision_id: events.newest,
      response_to_id: events.older,
      covers: [events.older],
    },
    expectedDecisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
    ],
    undecided: [events.newest],
  }, {
    toolCalls: [{
      tool: 'reply_message',
      input: {
        decision_id: events.newest,
        response_to_id: events.older,
      },
    }],
    decisions: [
      { event_id: events.newest, outcome: 'delivered', covered_by: null },
    ],
  });

  assert.equal(result.passed, false);
  assert.deepEqual(result.checks.filter(({ passed }) => !passed).map(({ name }) => name),
    ['tool-arguments', 'expected-decisions', 'undecided-inputs']);
});

test('requires stale rejection evidence before accepting a corrected retry', () => {
  const scenario = {
    id: 'stale-retry',
    expectedTool: {
      decision_id: events.newest,
      response_to_id: events.older,
      covers: [events.older],
    },
    expectedDecisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
    ],
    undecided: [events.newest],
    requireStaleDecision: events.older,
  };
  const baseEvidence = {
    toolCalls: [{
      tool: 'reply_message',
      input: {
        decision_id: events.newest,
        response_to_id: events.older,
        covers: [events.older],
      },
    }],
    decisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
    ],
  };

  assert.equal(assessScenario(scenario, { ...baseEvidence, staleDecisions: [] }).passed, false);
  assert.equal(assessScenario(scenario, {
    ...baseEvidence,
    staleDecisions: [{ event_id: events.older }],
  }).passed, true);
});

test('accepts protocol-equivalent tool and ledger alternatives', () => {
  const result = assessScenario({
    id: 'alternative',
    expectedTools: [
      {
        decision_id: events.newest,
        coversIncludes: [events.older],
      },
    ],
    expectedDecisionAlternatives: [
      [
        { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
        { event_id: events.newest, outcome: 'silent', covered_by: null },
      ],
      [
        { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
        { event_id: events.newest, outcome: 'delivered', covered_by: null },
      ],
    ],
    undecided: [],
  }, {
    toolCalls: [{
      tool: 'send_message',
      input: {
        decision_id: events.newest,
        covers: [events.older, events.newest],
      },
    }],
    decisions: [
      { event_id: events.older, outcome: 'delivered', covered_by: events.newest },
      { event_id: events.newest, outcome: 'delivered', covered_by: null },
    ],
  });

  assert.equal(result.passed, true);
});
