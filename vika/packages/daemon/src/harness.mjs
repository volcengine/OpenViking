// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { normalizeEvent, deterministicFilter } from './event.mjs';
import { commandCandidate, parseCommand } from './commands.mjs';
import { GroupBurstRouter } from './group-burst.mjs';
import { BOT_SENDERS, formatInboundLine, resolveReplyTarget, eventDeliveryItem, senderLabel } from './runtime-input.mjs';
import { opaqueRef, safeError } from './logger.mjs';
import { parseProtocolLine } from './connectors/protocol.mjs';

// Recall (ov-recall.mjs) reads OV on the ingress path, which every later
// line waits behind, so only where the agent is likely to answer: direct
// chats, lines that @mention it, document comments, and events outside chats
// such as mail. Never for another bot's line or a card click.
function wantsRecall(event, mention) {
  if (BOT_SENDERS.has(event.senderType) || event.messageType === 'card_action') return false;
  if (!event.chatId) return true;
  return event.chatType === 'p2p' || event.chatType === 'doc_comment' || mention === 'me' || mention === 'mixed';
}

// What an inbox notice says about a waiting input: where it is and who wrote
// it, never its text.
function inputMeta(event, { owner = false, mention = null } = {}) {
  return {
    chatId: event.chatId ?? null,
    threadId: event.threadId ?? null,
    source: event.source ?? null,
    instance: event.connectorInstance ?? null,
    kind: event.kind ?? null,
    chatType: event.chatId ? event.chatType ?? null : null,
    from: event.senderId || event.senderName ? senderLabel(event) : null,
    owner: Boolean(owner),
    mention: mention ?? null,
  };
}

// The harness: canonical Connector EVENT -> persistence -> AgentRuntime.submit.
// Owns validation, filtering, dedupe and ordered admission. Chat capabilities
// (slash commands, roster warm-up, burst batching) run only on eligible events.
// Provider decoding and outbound actions live outside this input pipeline.
export class Harness {
  constructor({
    config,
    store,
    logger,
    recordActivity,
    runtime,
    roster = null,
    liveness = null,
    recall = null,
    prepareEvent = (event) => event,
    eventHandler = () => null,
    onSlashCommand,
    approvalReply = () => null,
    onInboundChat = null,
    afterEvent = null,
    isOwner = () => false,
    mentionTarget = () => null,
    isStopping = () => false,
    inbox = null,
    taskHint = null,
  }) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this.recordActivity = recordActivity;
    this.runtime = runtime;
    this.roster = roster;
    this.liveness = liveness;
    this.recall = recall;
    this.prepareEvent = prepareEvent;
    this.eventHandler = eventHandler;
    this.onSlashCommand = onSlashCommand;
    this.approvalReply = approvalReply;
    this.onInboundChat = onInboundChat;
    this.afterEvent = afterEvent;
    this.isOwner = isOwner;
    this.mentionTarget = mentionTarget;
    this.isStopping = isStopping;
    // The inbox scheduler's waiting inputs (inbox.mjs); null under legacy.
    this.inbox = inbox;
    // Workers scheduler: a line naming the tasks an input may belong to.
    this.taskHint = taskHint;
    this.ingressBarrier = Promise.resolve();
    this.pendingIngress = 0;
    this.groupBurst = new GroupBurstRouter({
      submit: (items, options) => this.runtime.submit(items, options),
      isConnected: () => this.runtime.snapshot().session.connected,
      isTurnInProgress: () => this.runtime.snapshot().work.turnInProgress,
    });
  }

  handleConnectorEvent(envelope) {
    return this.handleEventLine(JSON.stringify(envelope));
  }

  handleEventLine(line) {
    if (this.isStopping()) return;
    if (Buffer.byteLength(line, 'utf8') > this.config.maxEventBytes) {
      this.logger.warn('event_dropped_oversize', { bytes: Buffer.byteLength(line, 'utf8') });
      this.recordActivity('system.event_dropped', {
        summary: '收到的事件体积超过本机限制',
        status: 'DROPPED',
        metadata: { reason: 'oversize', bytes: Buffer.byteLength(line, 'utf8') },
      });
      return;
    }
    let parsed;
    try {
      parsed = parseProtocolLine(line);
      if (parsed.type !== 'EVENT') throw new Error('expected a Connector EVENT');
    } catch (error) {
      this.logger.warn('event_invalid_json', { error: safeError(error) });
      this.recordActivity('system.event_dropped', {
        summary: '收到无法解析的 Connector 事件',
        status: 'DROPPED',
        metadata: { reason: 'invalid_json', error: safeError(error) },
      });
      return;
    }

    let event;
    try {
      event = this.prepareEvent(normalizeEvent(parsed));
    } catch (error) {
      this.logger.warn('event_invalid_shape', { error: safeError(error) });
      this.recordActivity('system.event_dropped', {
        summary: 'Connector 事件缺少必要字段',
        status: 'DROPPED',
        metadata: { reason: 'invalid_shape', error: safeError(error) },
      });
      return;
    }
    const filterReason = deterministicFilter(event, this.config);
    // Only eligible, replyable P2P messages can invoke local slash commands.
    // A slash in any other event body remains ordinary model input.
    const commandText = filterReason ? null : commandCandidate(event);
    const command = commandText === null ? null : parseCommand(commandText);
    // The owner answering a numbered draft (发送 N ...) is the Core's to
    // carry out, like a slash command; it never reaches the model as such.
    const approval = commandText === null || command ? null : this.approvalReply(commandText, event);
    const handler = this.eventHandler(event);
    let result;
    try {
      result = this.store.ingest(event, command ? 'slash_command' : approval ? 'approval_reply' : filterReason,
        Date.now(), { journal: !handler });
    } catch (error) {
      this.logger.error('event_persist_failed', { error: safeError(error) });
      this.recordActivity('system.persist_failed', {
        summary: safeError(error),
        status: 'FAILED',
      });
      return;
    }
    if (!result.inserted) {
      this.logger.debug('event_duplicate', { messageRef: opaqueRef(event.messageId) });
      this.recordActivity('message.duplicate', {
        summary: '同一来源事件已经接收过',
        status: 'DUPLICATE',
        conversationKey: event.conversationKey,
        metadata: { reason: 'deduplicated' },
      });
      return;
    }
    if (event.source === 'lark' && event.chatId) this.onInboundChat?.(event.chatId);
    if (command) {
      this.logger.info('event_slash_command', {
        messageRef: opaqueRef(event.messageId),
        chatRef: opaqueRef(event.chatId),
        command: command.name ?? 'unknown',
      });
      this.enqueueIngress(async () => {
        this.groupBurst.flushAll();
        await this.onSlashCommand(command, event, result.inbound);
      });
      return;
    }
    if (approval) {
      this.logger.info('event_approval_reply', { messageRef: opaqueRef(event.messageId), verb: approval.verb });
      // Off the ingress queue: an SMTP send may be slow.
      this.enqueueIngress(() => { approval.handle(event, result.inbound); });
      return;
    }
    if (result.filterReason) {
      this.logger.info('event_rule_ignored', {
        messageRef: opaqueRef(event.messageId),
        chatRef: opaqueRef(event.chatId),
        reason: result.filterReason,
      });
      return;
    }
    if (this.afterEvent) {
      void Promise.resolve().then(() => this.afterEvent(event)).catch(() => {
        this.logger.warn('event_followup_failed');
      });
    }
    if (handler) {
      return this.enqueueIngress(() => handler(event));
    }
    this.logger.info('event_delivered', {
      seq: result.inbound.seq,
      messageRef: opaqueRef(event.messageId),
      chatRef: opaqueRef(event.chatId),
      conversationRef: opaqueRef(event.conversationKey),
      chatType: event.chatType,
    });
    this.enqueueIngress(async () => {
      const idleHint = event.chatId && event.replyRoute && this.liveness?.beforeInbound(event, result.inbound, {
        modelBusy: this.runtime.snapshot().work.turnInProgress,
      });
      // Bounded roster warm-up so formatInboundLine can resolve from= to a
      // display name. warm() never rejects and races an internal timeout, so a
      // slow or failing lark-cli only degrades the label back to the id stub.
      // A document has no members to list: its Connector names the commenter.
      if (event.source === 'lark' && event.chatId && event.chatType !== 'doc_comment') await this.roster?.warm(event.chatId);
      const owner = this.isOwner(event);
      const mention = this.mentionTarget(event);
      // Bounded like the warm-up above, and never rejects.
      const recall = this.recall && wantsRecall(event, mention) ? await this.recall.forEvent(event, { owner }) : '';
      await this.runtime.prepareInputContext?.();
      const line = formatInboundLine(event, null, {
        ...(idleHint ?? {}),
        owner,
        mention,
        replyTo: event.messageType === 'card_action' ? { kind: 'me' } : resolveReplyTarget(this.store, event),
        // How long only bots have been talking here: the model's cue to stop.
        ...(BOT_SENDERS.has(event.senderType)
          ? { botsSinceHuman: this.store.botsSincePerson(event.scopeKey, result.inbound.seq) } : {}),
      });
      const hint = event.chatId ? this.taskHint?.(event, { owner }) ?? '' : '';
      this.route(eventDeliveryItem(event, result.inbound.seq, { text: [line, hint, recall].filter(Boolean).join('\n') }),
        inputMeta(event, { owner, mention }));
    });
  }

  // Every input line's way to the runtime: the inbox scheduler's inbox, or
  // the legacy group burst. A runtime that cannot read the inbox (no Core
  // tools) gets inputs the legacy way, after anything still waiting there.
  route(item, meta) {
    if (this.inbox?.usable()) {
      this.inbox.add(item, meta);
      return;
    }
    this.inbox?.drain();
    this.groupBurst.route(item);
  }

  // A line the Core composed about an accepted chat event (what an approval
  // card actually did), for the runtime in that event's chat. It reports on an
  // inbound rather than being one, so it carries no seq.
  deliverNote(event, content) {
    return this.enqueueIngress(async () => {
      await this.runtime.prepareInputContext?.();
      const owner = this.isOwner(event);
      this.route(eventDeliveryItem(event, null, {
        text: formatInboundLine({ ...event, content }, null, { owner, replyTo: { kind: 'me' } }),
      }), inputMeta(event, { owner }));
    });
  }

  // Shows an accepted line again, tagged undecided=1, after a turn ended with
  // neither a delivery nor stay_silent for it. Same event= (still a valid
  // decision_id), but no seq: it never moves the fence or counts as a new
  // input, so it cannot start another reminder. A line a loop-stopped turn
  // left behind comes back with its seq instead (a second stop must see it
  // again), and with the tag only where the policy explains it.
  redeliverUndecided(row, { seq = null, undecided = true } = {}) {
    return this.enqueueIngress(async () => {
      await this.runtime.prepareInputContext?.();
      const owner = this.isOwner(row);
      const mention = this.mentionTarget(row);
      this.route(eventDeliveryItem(row, seq, {
        text: formatInboundLine(row, null, {
          owner,
          mention,
          replyTo: row.messageType === 'card_action' ? { kind: 'me' } : resolveReplyTarget(this.store, row),
          undecided,
        }),
      }), inputMeta(row, { owner, mention }));
    });
  }

  // A slash command and every later accepted message share one global order.
  // Slow model discovery and two-phase runtime catch-up therefore cannot let a
  // following message slip into the old runtime.
  enqueueIngress(action) {
    this.pendingIngress += 1;
    const run = this.ingressBarrier.then(async () => {
      if (!this.isStopping()) await action();
    }).finally(() => { this.pendingIngress -= 1; });
    this.ingressBarrier = run.catch((error) => {
      this.logger.error('ingress_barrier_action_failed', { error: safeError(error) });
    });
    return run;
  }

  // Hand every already-arrived group batch to the current runtime, e.g. before
  // a /runtime or /new control action can overtake it.
  flushBufferedIngress() {
    return this.groupBurst.flushAll();
  }

  // The runtime became idle: give buffered group bursts a chance to claim it.
  onAvailable() {
    this.groupBurst.onAvailable();
    this.inbox?.onAvailable();
  }

  stop() {
    this.groupBurst.stop();
    this.inbox?.stop();
  }
}
