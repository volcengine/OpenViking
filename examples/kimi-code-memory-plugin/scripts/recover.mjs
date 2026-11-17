#!/usr/bin/env node

/**
 * Explicit failure recovery — not a hook.
 *
 * A capture that permanently failed, or a queued message the queue dropped,
 * blocks commits on purpose: the alternative is archiving a transcript with a
 * hole and calling it complete. But a plugin must not be able to lock a user
 * out of their own memory forever either. This entry is the human's way out,
 * and it is deliberately awkward:
 *
 *   retry-commit   clear one agent's lost-commit blockage so the next boundary
 *                  retries. It refuses when a message is still outstanding,
 *                  because retrying a commit over undelivered messages is
 *                  exactly the hole the block exists to prevent.
 *   accept-loss    advance the cursor past a recorded failure and write a
 *                  tombstone. Requires `--yes`; `--all-agents` requires it too.
 *
 * Nothing here touches the network, deletes wire files, or removes memories.
 * Everything happens under the same session lock the hooks use, and every
 * action records who did it and why.
 */

import { parseArgs as parseNodeArgs } from "node:util";

import { readHookStateDetailed, withAgentHookLock } from "./shared/agent-hook-runtime.mjs";
import { listPending } from "./shared/pending-queue.mjs";
import { HARNESS } from "./config.mjs";
import {
  acceptLoss,
  emptySessionState,
  pendingForSession,
  retryCommit,
  selectAgents,
} from "./session-state.mjs";
import { writeHookState } from "./shared/agent-hook-runtime.mjs";

const USAGE = `usage:
  node ./scripts/recover.mjs retry-commit --session <nativeSessionId> --agent <agentId>
  node ./scripts/recover.mjs accept-loss --session <nativeSessionId> --agent <agentId> --yes
  node ./scripts/recover.mjs accept-loss --session <nativeSessionId> --all-agents --yes

  --session <id>    the Kimi Code session id (required)
  --agent <id>      the agent whose OpenViking session is affected; may be
                    omitted only when the session has a single agent
  --all-agents      every agent of the session (accept-loss only)
  --yes             confirm the data loss this action records
  --reason <text>   recorded with the action, for the audit trail
`;

function fail(message) {
  process.stderr.write(`recover: ${message}\n`);
  process.exitCode = 1;
  return undefined;
}

function parseCli(argv) {
  const { values, positionals } = parseNodeArgs({
    args: argv,
    allowPositionals: true,
    options: {
      session: { type: "string" },
      agent: { type: "string" },
      "all-agents": { type: "boolean" },
      yes: { type: "boolean" },
      reason: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });
  return { action: positionals[0], ...values };
}

async function main() {
  const cli = parseCli(process.argv.slice(2));
  if (cli.help || !cli.action) {
    process.stdout.write(USAGE);
    process.exitCode = cli.action ? 0 : 2;
    return;
  }
  if (cli.action !== "retry-commit" && cli.action !== "accept-loss") {
    fail(`unknown action '${cli.action}'`);
    return;
  }
  const sessionId = String(cli.session || "").trim();
  if (!sessionId) {
    fail("--session is required");
    return;
  }
  if (cli.action === "accept-loss" && cli.yes !== true) {
    fail("accept-loss discards data from the record; pass --yes to confirm");
    return;
  }
  if (cli.action === "retry-commit" && cli["all-agents"]) {
    fail("retry-commit takes one agent; pass --agent <id>");
    return;
  }
  if (cli["all-agents"] && cli.agent) {
    fail("--agent and --all-agents are mutually exclusive");
    return;
  }

  const read = await readHookStateDetailed(HARNESS, sessionId);
  if (read.status === "missing") {
    fail(`no plugin state for session '${sessionId}'`);
    return;
  }
  if (read.status !== "ok") {
    fail(`state for '${sessionId}' is ${read.status}: ${read.error || "unreadable"}`);
    return;
  }
  const state = { ...emptySessionState(), ...read.state };

  const selection = selectAgents(state, { agent: cli.agent, allAgents: cli["all-agents"] === true });
  if (selection.error) {
    fail(selection.error);
    return;
  }

  const reason = String(cli.reason || "");
  const result = await withAgentHookLock(HARNESS, sessionId, async () => {
    // Re-read under the lock: another hook may have written between the first
    // read and now, and this action must apply to the current state.
    const current = await readHookStateDetailed(HARNESS, sessionId);
    if (current.status !== "ok") return { ok: false, error: `state became ${current.status}` };
    const live = { ...emptySessionState(), ...current.state };

    // The queue is read here, under the lock, and its real contents are what
    // the preconditions are about. A state file cannot know what another
    // process queued, so a guard that trusted one would be answering a question
    // nobody asked.
    const pending = await listPending();
    const queuedFor = (agent) => {
      const { messages, commits } = pendingForSession(pending, agent?.ovSessionId);
      return { messages, commits };
    };

    if (cli.action === "retry-commit") {
      const agentId = selection.agents[0];
      const { messages, commits } = queuedFor(live.agents[agentId]);
      const otherCommits = commits.length;
      const outcome = retryCommit(live, agentId, {
        reason,
        pendingMessages: messages.length,
        otherCommits,
      });
      if (!outcome.ok) return outcome;
      // The ended marker stays: this only unblocks the commit, it does not
      // declare the session finished.
      await writeHookState(HARNESS, sessionId, outcome.state);
      return outcome;
    }

    const pendingMessagesByAgent = {};
    for (const agentId of selection.agents) {
      pendingMessagesByAgent[agentId] = queuedFor(live.agents[agentId]).messages.length;
    }
    const outcome = acceptLoss(live, selection.agents, { reason, pendingMessagesByAgent });
    if (!outcome.ok) return outcome;
    await writeHookState(HARNESS, sessionId, outcome.state);
    return outcome;
  });

  if (result === null) {
    fail("session is locked by a running hook; try again at the next boundary");
    return;
  }
  if (!result.ok) {
    fail(result.error);
    return;
  }

  // Report what is still outstanding, so the operator knows whether the session
  // will settle on its own or needs another action.
  const pending = await listPending();
  const ovIds = Object.values(result.state.agents || {}).map((agent) => agent.ovSessionId);
  const outstanding = ovIds.reduce((total, id) => {
    const { messages, commits } = pendingForSession(pending, id);
    return total + messages.length + commits.length;
  }, 0);

  const summary = {
    action: cli.action,
    sessionId,
    agents: cli.action === "retry-commit" ? selection.agents : result.accepted,
    ended: result.ended === true,
    pendingRemaining: outstanding,
    // Recorded with the action so a maintainer can see who did it and why
    // without reading a transcript.
    operator: "cli",
    reason: reason || undefined,
    warning: result.warning,
  };
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}

const isDirectRun = process.argv[1]
  && /recover\.mjs$/i.test(process.argv[1].replace(/\\/g, "/"));

if (isDirectRun) {
  main().catch((error) => {
    process.stderr.write(`recover: ${error?.message || error}\n`);
    process.exitCode = 1;
  });
}
