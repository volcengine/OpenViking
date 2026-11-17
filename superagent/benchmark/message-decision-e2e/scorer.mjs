import assert from 'node:assert/strict';

function same(value, expected) {
  try {
    assert.deepEqual(value, expected);
    return true;
  } catch {
    return false;
  }
}

function normalizedDecision(row) {
  return {
    event_id: row.event_id,
    outcome: row.outcome,
    covered_by: row.covered_by ?? null,
  };
}

export function assessScenario(scenario, evidence) {
  const finalCalls = (evidence.toolCalls ?? []).filter(({ tool, input }) =>
    ['send_message', 'reply_message', 'react_to_message', 'retract_message', 'notify_owner'].includes(tool)
    && input?.interim !== true && input?.temporary !== true);
  const expectedTools = scenario.expectedTools ?? [scenario.expectedTool];
  const toolArguments = expectedTools.every((expectedTool) => finalCalls.some(({ input }) =>
    Object.entries(expectedTool).every(([key, expected]) => {
      if (key === 'coversIncludes') {
        return Array.isArray(input?.covers) && expected.every((eventId) => input.covers.includes(eventId));
      }
      return same(input?.[key], expected);
    })));

  const alternatives = scenario.expectedDecisionAlternatives ?? [scenario.expectedDecisions];
  const expectedDecisions = alternatives.map((rows) => rows.map(normalizedDecision)
    .sort((one, two) => one.event_id.localeCompare(two.event_id)));
  const actualDecisions = (evidence.decisions ?? []).map(normalizedDecision)
    .sort((one, two) => one.event_id.localeCompare(two.event_id));
  const decided = new Set(actualDecisions.map(({ event_id }) => event_id));
  const undecided = scenario.undecided.every((eventId) => !decided.has(eventId));
  const stale = !scenario.requireStaleDecision
    || (evidence.staleDecisions ?? []).some(({ event_id: eventId }) => eventId === scenario.requireStaleDecision);

  const checks = [
    { name: 'tool-arguments', passed: Boolean(toolArguments) },
    { name: 'expected-decisions', passed: expectedDecisions.some((expected) => same(actualDecisions, expected)) },
    { name: 'undecided-inputs', passed: undecided },
    ...(scenario.requireStaleDecision ? [{ name: 'stale-rejection', passed: stale }] : []),
  ];
  return { passed: checks.every(({ passed }) => passed), checks };
}
