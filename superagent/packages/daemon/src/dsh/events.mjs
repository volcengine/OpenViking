// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Event boundary: what DSH reports about the running session is projected here,
// and nowhere else. A `session/status` notification may open an autonomous turn;
// a `session.event` becomes an activity record (notes, thinking, tool calls and
// their results, token usage) or the `turn/end` marker the settlement loop reads.
// Nothing here writes to the transport: the only outbound effect is a turn it
// opens, which then goes through the turn module as usual.
import { randomUUID } from 'node:crypto';
import { safeError as safe } from '../logger.mjs';
import { activityPayload } from '../activity.mjs';
import { number } from './shared.mjs';

// `mcp__<server>__<tool>` is the flattened name the bridge exposes; OpenViking SuperAgent reports
// the server and the tool separately, and labels them together for the activity.
function toolNameParts(name) {
  const match = /^mcp__(.*?)__(.+)$/.exec(String(name ?? ''));
  return match
    ? { toolServer: match[1], toolName: match[2], label: `${match[1]} · ${match[2]}` }
    : { toolName: String(name ?? ''), label: String(name ?? '') || 'unknown tool' };
}

export default {
  _handleNotification(message) {
    const params = message.params ?? {};
    if (params.sessionId !== this.sessionId) return;
    if (message.method === 'session.status') {
      this.modelWorking = params.status === 'running';
      // A settings application in flight is a turn boundary that has not
      // finished: an autonomous turn opened here would start on the route this
      // process still has while `appliedModel` is about to change. The status
      // stays `running`, and the next boundary opens the turn instead.
      if (params.status === 'running' && !this.turn && this.inflight.size === 0
        && !this.restarting && !this.stopping && !this.settingsUpdate) {
        this._openTurn({ autonomous: true });
      }
      return;
    }
    if (message.method !== 'session.event') return;
    this._handleSessionEvent(params.event);
  },

  _handleSessionEvent(event) {
    if (!event || typeof event !== 'object') return;
    const turn = this.turn;
    const data = event.data ?? {};
    switch (event.type) {
      case 'user/message': {
        // Observation is recorded even without a live turn: the ACK that
        // follows may still belong to the turn this message was steered into.
        if (data.id) this._observeMessage(data.id);
        if (!turn) return;
        if (turn.autonomous && turn.origin === null) {
          turn.origin = data.source?.kind ?? 'unknown';
          turn.startRecorded = false;
          this._recordAutonomousStart(turn);
        }
        return;
      }
      case 'assistant/message': {
        const message = data.message ?? {};
        const seqs = this._activeSeqs();
        for (const block of message.content ?? []) {
          if (block?.type === 'text' && block.text) {
            this._recordActivity('agent.note', {
              inboundSeq: seqs.at(-1) ?? null,
              summary: String(block.text),
              metadata: { runtime: this.runtime, delivery: 'telemetry_only', inboundSeqs: seqs },
            });
          } else if (block?.type === 'reasoning' && block.text) {
            this._recordActivity('agent.thinking', {
              inboundSeq: seqs.at(-1) ?? null,
              metadata: { runtime: this.runtime, thinking: String(block.text) },
            });
          }
        }
        if (turn && data.usage) {
          const current = turn.usage ?? {
            inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0,
          };
          turn.usage = {
            // DSH input/cache counters are disjoint; OpenViking SuperAgent inputTokens includes
            // cached input and also retains the cache-specific breakdown.
            inputTokens: current.inputTokens + number(data.usage.inputTokens) + number(data.usage.cacheReadTokens),
            cachedInputTokens: current.cachedInputTokens + number(data.usage.cacheReadTokens),
            outputTokens: current.outputTokens + number(data.usage.outputTokens),
            reasoningTokens: current.reasoningTokens + number(data.usage.reasoningTokens),
          };
        }
        return;
      }
      case 'tool/call': {
        this.modelWorking = false;
        const parts = toolNameParts(data.name);
        const callId = String(data.callId ?? randomUUID());
        this.toolStarts.set(callId, { atMs: Date.now(), ...parts });
        let input;
        try { input = JSON.parse(data.arguments); } catch { input = data.arguments; }
        this._recordActivity('agent.tool_called', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          status: 'RUNNING',
          summary: parts.label,
          metadata: {
            runtime: this.runtime,
            toolId: callId,
            toolName: parts.toolName,
            ...(parts.toolServer ? { toolServer: parts.toolServer } : {}),
            input: activityPayload(input),
          },
        });
        return;
      }
      case 'tool/result': {
        const message = data.message ?? {};
        const callId = String(message.toolCallId ?? '');
        const started = this.toolStarts.get(callId);
        this.toolStarts.delete(callId);
        const parts = started ?? toolNameParts(message.toolName);
        const text = (message.content ?? [])
          .filter((block) => block?.type === 'text')
          .map((block) => String(block.text ?? '')).join('\n');
        const failed = message.isError === true || Boolean(data.error);
        const output = activityPayload(text);
        this._recordActivity('agent.tool_completed', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          status: failed ? 'FAILED' : 'COMPLETED',
          summary: parts.label,
          metadata: {
            runtime: this.runtime,
            toolId: callId,
            toolName: parts.toolName,
            ...(parts.toolServer ? { toolServer: parts.toolServer } : {}),
            ...(started ? { durationMs: Date.now() - started.atMs } : {}),
            ...(failed
              ? { error: activityPayload(data.error?.reason ?? data.error ?? text) }
              : { output }),
          },
        });
        this.modelWorking = Boolean(this.turn);
        return;
      }
      case 'turn/end':
        if (turn && data.reason?.kind !== 'forked') turn.lastEnd = data;
        return;
      default:
        return;
    }
  },

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      // The per-run generation keeps two daemon runs that resume the same DSH
      // session from colliding on the store's UNIQUE activity key.
      this.store.recordActivity?.({
        eventKey: `dsh:${this.sessionId ?? 'na'}:${this.runId}:${kind}:${this.activitySeq}`,
        kind,
        summary: fields.summary,
        status: fields.status,
        inboundSeq: fields.inboundSeq,
        metadata: fields.metadata,
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind, error: safe(error) });
    }
  },
};
