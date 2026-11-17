import fs from 'node:fs';
import path from 'node:path';
import { startMockEnv, textMessage } from '../../packages/daemon/test/mock-env.mjs';
import { assessScenario } from './scorer.mjs';
import { scenarioById, scenarios } from './scenarios.mjs';
import { createIsolatedTraex } from './environment.mjs';

const args = process.argv.slice(2);
const value = (flag, fallback) => args.includes(flag) ? args[args.indexOf(flag) + 1] : fallback;
const smoke = args.includes('--smoke');
const output = path.resolve(value('--out', './benchmark-results/message-decision-e2e'));
const selected = value('--scenario', smoke ? 'combined' : 'all');
const chosen = selected === 'all' ? scenarios : [scenarioById(selected)];
const model = value('--model', 'GPT-5.6-Sol');
const binary = value('--bin', '/Users/bytedance/.local/bin/traecli');
const authPath = value('--auth-file', '/Users/bytedance/.trae/cli/auth.json');
const timeoutMs = Number(value('--timeout', smoke ? '20' : '120')) * 1000;
fs.mkdirSync(output, { recursive: true, mode: 0o700 });

function parseActivity(row) {
  return { ...row, metadata: JSON.parse(row.detail_json || '{}') };
}

function evidenceFrom(env) {
  const activities = env.daemon.store.db.prepare(
    'SELECT kind, status, summary, inbound_seq, detail_json, occurred_at_ms FROM activities ORDER BY id',
  ).all().map(parseActivity);
  const toolCalls = [
    ...env.codexInput().filter(({ method }) => method === 'action-call')
      .map(({ tool, input }) => ({ tool, input })),
    ...activities.filter(({ kind, metadata }) => kind === 'agent.tool_called' && metadata.input)
      .map(({ metadata }) => {
        let input = metadata.input;
        try { input = typeof input === 'string' ? JSON.parse(input) : input; } catch {}
        return { tool: String(metadata.toolName ?? '').replace(/^mcp__personal_agent__/, ''), input };
      }),
  ];
  const decisions = env.daemon.store.db.prepare(
    'SELECT event_id, outcome, covered_by, seq FROM decisions ORDER BY seq',
  ).all();
  const deliveries = env.daemon.store.db.prepare(
    'SELECT action, status, payload_json, inbound_seq, sent_at_ms FROM outbox ORDER BY id',
  ).all().map((row) => ({ ...row, payload: JSON.parse(row.payload_json) }));
  const staleDecisions = activities.filter(({ kind, metadata }) =>
    kind === 'delivery.blocked' && metadata.code === 'stale_decision')
    .map(({ inbound_seq: seq }) => ({
      seq,
      event_id: env.daemon.store.db.prepare('SELECT event_id FROM connector_events WHERE seq = ?').get(seq)?.event_id,
    }));
  return { toolCalls, decisions, deliveries, activities, staleDecisions };
}

async function runScenario(scenario) {
  const directory = path.join(output, scenario.id);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  let env;
  let error = null;
  try {
    const isolated = smoke ? null : createIsolatedTraex(path.join(directory, 'runtime'), {
      authPath,
      binary,
    });
    env = await startMockEnv({
      config: {
        explicitSilence: true,
        naturalWriting: false,
        decisionReminderDelayMs: 1_000,
        decisionReminderLimit: 1,
        ...(smoke ? {} : {
          initialModel: `traex:${model}`,
          traexBin: isolated.bin,
        }),
      },
      codex: { behavior: smoke ? 'covers-reply' : 'complete' },
    });
    const inject = (message) => {
      env.receive(textMessage({
        messageId: `om_e2e_${scenario.id}_${message.suffix}`,
        eventId: eventId(scenario.id, message.suffix),
        chatId: `oc_e2e_${scenario.id}`,
        chatType: 'group',
        senderId: 'ou_mockOwner12345',
        text: message.text,
      }));
    };
    inject(scenario.messages[0]);
    if (scenario.injectAfterTurnStarted) {
      await env.waitFor(() => env.daemon.agentRuntime.snapshot().work.turnInProgress, timeoutMs);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    for (const message of scenario.messages.slice(1)) inject(message);
    try {
      await env.waitFor(() => {
        const decided = env.daemon.store.db.prepare('SELECT COUNT(*) AS count FROM decisions').get().count;
        const expectedCount = (scenario.expectedDecisions
          ?? scenario.expectedDecisionAlternatives?.[0] ?? []).length;
        return decided >= expectedCount;
      }, timeoutMs);
      await env.waitFor(() => env.daemon.store.db.prepare(
        "SELECT COUNT(*) AS count FROM outbox WHERE status = 'PENDING'",
      ).get().count === 0, Math.min(timeoutMs, 10_000));
    } catch (failure) {
      error = failure.message;
    }
    const evidence = evidenceFrom(env);
    if (scenario.observeStaleDecision && evidence.staleDecisions.length === 0) {
      scenario = { ...scenario, requireStaleDecision: undefined };
    }
    const result = {
      scenario: scenario.id,
      smoke,
      model: smoke ? 'fake-codex' : model,
      error,
      turnInProgressAtCapture: env.daemon.agentRuntime.snapshot().work.turnInProgress,
      ...evidence,
      assessment: assessScenario(scenario, evidence) };
    const resultPath = path.join(directory, 'result.json');
    fs.writeFileSync(resultPath, JSON.stringify(result, null, 2));
    return { scenario: scenario.id, passed: result.assessment.passed, error, resultPath };
  } finally {
    await env?.stop();
  }
}

function eventId(scenario, suffix) {
  return `evt-om_e2e_${scenario}_${suffix}`;
}

const results = [];
for (const scenario of chosen) results.push(await runScenario(scenario));
const summary = { smoke, passed: results.every(({ passed }) => passed), results };
fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary));
if (!summary.passed) process.exitCode = 1;
