const event = (scenario, suffix) => `evt-om_e2e_${scenario}_${suffix}`;

export const scenarios = [
  {
    id: 'combined',
    messages: [
      { suffix: 'a', text: '第一条：请计算 2+2。' },
      { suffix: 'b', text: '第二条：请计算 3+3，并用一条回复同时回答前两条。' },
    ],
    expectedTool: {
      decision_id: event('combined', 'b'),
      covers: [event('combined', 'a'), event('combined', 'b')],
    },
    expectedDecisions: [
      { event_id: event('combined', 'a'), outcome: 'delivered', covered_by: event('combined', 'b') },
      { event_id: event('combined', 'b'), outcome: 'delivered', covered_by: null },
    ],
    undecided: [],
  },
  {
    id: 'older-with-newest-fence',
    messages: [
      { suffix: 'a', text: '请只回答这个问题：法国首都是哪里？' },
      { suffix: 'b', text: '这是新的独立输入：不要在回答法国首都时把本条标为已处理；本条请单独决定无需回复。' },
    ],
    expectedTools: [
      {
        decision_id: event('older-with-newest-fence', 'b'),
        covers: [event('older-with-newest-fence', 'a')],
      },
    ],
    expectedDecisions: [
      { event_id: event('older-with-newest-fence', 'a'), outcome: 'delivered',
        covered_by: event('older-with-newest-fence', 'b') },
      { event_id: event('older-with-newest-fence', 'b'), outcome: 'silent', covered_by: null },
    ],
    undecided: [],
  },
  {
    id: 'separate',
    messages: [
      { suffix: 'a', text: '第一个独立问题：5+5 等于多少？请单独回答。' },
      { suffix: 'b', text: '第二个独立问题：6+6 等于多少？请单独回答，不要漏掉前一条。' },
    ],
    expectedTools: [
      { covers: [event('separate', 'a')] },
      { decision_id: event('separate', 'b') },
    ],
    expectedDecisions: [
      { event_id: event('separate', 'a'), outcome: 'delivered', covered_by: event('separate', 'b') },
      { event_id: event('separate', 'b'), outcome: 'delivered', covered_by: null },
    ],
    undecided: [],
  },
  {
    id: 'concurrent-update',
    injectAfterTurnStarted: true,
    messages: [
      { suffix: 'a', text: '请先思考后回答：日本首都是哪里？' },
      { suffix: 'b', text: '补充输入：上一条仍然要回答；本条无需单独回复。' },
    ],
    expectedTools: [
      {
        decision_id: event('concurrent-update', 'b'),
        coversIncludes: [event('concurrent-update', 'a')],
      },
    ],
    expectedDecisionAlternatives: [
      [
        { event_id: event('concurrent-update', 'a'), outcome: 'delivered', covered_by: event('concurrent-update', 'b') },
        { event_id: event('concurrent-update', 'b'), outcome: 'silent', covered_by: null },
      ],
      [
        { event_id: event('concurrent-update', 'a'), outcome: 'delivered', covered_by: event('concurrent-update', 'b') },
        { event_id: event('concurrent-update', 'b'), outcome: 'delivered', covered_by: null },
      ],
    ],
    undecided: [],
    observeStaleDecision: true,
  },
];

export function scenarioById(id) {
  const scenario = scenarios.find((entry) => entry.id === id);
  if (!scenario) throw new Error(`Unknown scenario: ${id}`);
  return scenario;
}
