// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash, randomUUID } from 'node:crypto';
import { conversationKeyFor, eventIdentity } from '../event.mjs';
import { registerCard } from '../card-messages.mjs';
import {
  cancelApproval, claimApproval, createApproval, editApproval, finishApproval, getApproval, supersedeApprovals,
} from '../approvals.mjs';
import { chunkOffset, mailArchiveRoot, readStoredText, readTarget } from '../email/archive.mjs';
import { meetingContextLines } from '../meeting/index.mjs';
import { OVUnavailableError } from '../ov-connection.mjs';
import { OVSessionClient, withoutWorkingMemory } from '../ov-session-context.mjs';
import { opaqueRef, redactSensitive, safeError } from '../logger.mjs';
import { BOT_SENDERS, readMetadataTag } from '../runtime-input.mjs';
import { TimerSchedule } from '../timers.mjs';
import {
  APPROVAL_SECONDS, approvalCard, approvalMessage, approvalNote, approvalQuestion, approvalStatusText, approvedText,
  parseApprovalReply, settledApprovalCard,
} from './approval.mjs';
import { resolveContent } from './content.mjs';
import { TOOLS, publicTool, toolDefinition, validateInput } from './tools.mjs';

const NO_ACTION = 'No action was taken.';
// Replies to bot messages in one chat/thread with no person writing there,
// after which the safety fuse holds further ones (VIKA_BOT_REPLY_FUSE).
const BOT_REPLY_FUSE = 20;

const HANDLERS = {
  send_message: 'sendMessage',
  notify_owner: 'notifyOwner',
  reply_message: 'replyMessage',
  react_to_message: 'reactToMessage',
  retract_message: 'retractMessage',
  stay_silent: 'staySilent',
  list_chats: 'listChats',
  list_chat_members: 'listChatMembers',
  list_chat_messages: 'listChatMessages',
  search_messages: 'searchMessages',
  list_thread_messages: 'listThreadMessages',
  get_message_media: 'getMessageMedia',
  read_email: 'readEmail',
  read_openviking_session_context: 'readOVSession',
  read_meeting_inbox: 'readMeetingInbox',
  get_time: 'getTime',
  create_timer: 'createTimer',
  list_timers: 'listTimers',
  cancel_timer: 'cancelTimer',
  complete_timer_occurrence: 'completeTimerOccurrence',
};

// A refusal the model can act on; `code` is what logs and activities record.
function blocked(code, message) {
  return Object.assign(new Error(message), { code });
}

function actionKey(...parts) {
  return `ld_${createHash('sha256').update(JSON.stringify(parts)).digest('hex').slice(0, 40)}`;
}

// An inbound event in an idempotency key: the same event whichever id named it,
// and distinct per instance.
function eventRef(row) {
  return [row.connector_instance, row.event_id];
}

function timerDelivery(row) {
  if (row?.connector_instance !== 'timer' || row?.kind !== 'reminder.due' || !row.event_json) return null;
  try {
    const delivery = JSON.parse(row.event_json).data?.timer?.delivery;
    return delivery && typeof delivery === 'object' ? delivery : null;
  } catch {
    return null;
  }
}

function messageLines(messages, { withChat = false } = {}) {
  return messages.map((message) => {
    const sender = message.sender.name || message.sender.id || 'unknown';
    const chat = withChat && message.chat_id ? ` | chat_id=${message.chat_id}` : '';
    const thread = message.thread_id ? ` | thread_id=${message.thread_id}` : '';
    return `- ${message.message_id}${chat}${thread} | ${sender} | ${message.msg_type} | ${message.content}`;
  }).join('\n');
}

function morePages(data) {
  if (!data.has_more) return '';
  return data.next_cursor ? `\nMore results exist; repeat the same query with cursor=${JSON.stringify(data.next_cursor)}.`
    : '\nMore results exist; narrow the query or raise limit.';
}

// The single outbound path. Agent tool calls (through the MCP adapter and the
// local socket) and daemon notices all end in deliver(): reserve in the
// outbox, invoke the Connector, settle. Authority, freshness, routing and
// content checks happen here once, whichever runtime asked.
export class ActionService {
  constructor({
    config,
    store,
    connectors,
    roster = null,
    liveness = null,
    meetingEnabled = false,
    ovClient = null,
    timers = null,
    clock = () => Date.now(),
    onTimersChanged = () => {},
    logger,
  }) {
    Object.assign(this, {
      config, store, connectors, roster, liveness, meetingEnabled, ovClient, timers, clock, onTimersChanged, logger,
    });
    // One temporary working reaction per conversation, removed by the next
    // final delivery there.
    this.temporaryReactions = new Map();
    // Removals of them still under way, which close() waits for.
    this.reactionRemovals = new Set();
    // Query context may identify a reply target, but never an input/decision.
    this.queryTargets = new Map();
  }

  manifest(connector) {
    return new Map(this.connectors.manifests()).get(connector) ?? null;
  }

  supporting(operation, kind) {
    return this.connectors.manifests()
      .filter(([, manifest]) => (kind === 'actions' ? Boolean(manifest.actions[operation]) : manifest.queries.includes(operation)))
      .map(([id]) => id);
  }

  offered(tool) {
    if (tool.action) return this.supporting(tool.action, 'actions').length > 0;
    if (tool.query) return this.supporting(tool.query, 'queries').length > 0;
    if (tool.connector) return this.connectors.manifests().some(([, manifest]) => manifest.name === tool.connector);
    if (tool.meeting) return this.meetingEnabled;
    if (tool.openviking) return Boolean(this.ovClient || this.config.openVikingConfigPath);
    if (tool.timer) return Boolean(this.timers);
    if (tool.explicitSilence) return Boolean(this.config.explicitSilence);
    return true;
  }

  tools() {
    return TOOLS.filter((tool) => this.offered(tool)).map((tool) => publicTool(this.definition(tool)));
  }

  definition(tool) {
    return toolDefinition(tool, {
      explicitSilence: Boolean(this.config.explicitSilence), stagedReplies: Boolean(this.config.stagedReplies),
    });
  }

  // Agent entrypoint. Returns { text, data, media? }; throws a model-readable
  // error with a `code`. Each call logs one `agent_tool` line whose seqs and
  // outbox id line up with connector_events/outbox; a blocked delivery is also
  // kept as a delivery.blocked activity.
  async call(name, input = {}, { authority = null, via = null } = {}) {
    const startedAt = Date.now();
    const trace = { tool: name, via };
    const caller = { authority, trace };
    const tool = TOOLS.find((candidate) => candidate.name === name);
    // A refused stay_silent is shown like a blocked delivery, only while the
    // tool is offered (explicitSilence on).
    const silence = name === 'stay_silent' && Boolean(this.config.explicitSilence);
    try {
      if (!tool || !this.offered(tool)) throw blocked('unknown_tool', `unknown tool: ${name}`);
      try { validateInput(this.definition(tool).inputSchema, input); } catch (error) { throw blocked('invalid_input', error.message); }
      this.assertAuthority(authority);
      trace.runtime = this.store.getCognitionState().runtime;
      let result = await this[HANDLERS[name]](input, caller);
      // Only a call that changed nothing says STOP: a success after a refused
      // attempt (decision_ambiguous fixed with decision_instance) is a fix.
      if (silence && result.data?.duplicate) {
        result = { ...result, text: `STOP: this stay_silent call changed nothing for this input. Do not call it again for it; decide any other input you still have, otherwise finish your turn. ${result.text}` };
      }
      trace.outcome = result.data?.dry_run ? 'dry_run' : result.data?.duplicate ? 'duplicate' : 'ok';
      this.logger.info('agent_tool', { ...trace, durationMs: Date.now() - startedAt });
      return result;
    } catch (error) {
      trace.outcome = trace.invoked ? 'failed' : 'blocked';
      trace.code = error.code ?? (trace.invoked ? 'connector_failed' : 'error');
      this.logger.warn('agent_tool', { ...trace, durationMs: Date.now() - startedAt, error: safeError(error) });
      if (trace.outcome === 'blocked' && (tool?.action || silence)) {
        this.store.recordActivity({
          eventKey: `tool:${randomUUID()}`, kind: 'delivery.blocked', status: 'BLOCKED',
          conversationKey: caller.convKey ?? null, inboundSeq: trace.decisionSeq ?? null, summary: safeError(error),
          metadata: { tool: name, code: trace.code, connector: trace.connector, via, runtime: trace.runtime,
            decisionSeq: trace.decisionSeq, newerSeq: trace.newerSeq },
        });
      }
      throw error;
    }
  }

  // Daemon-authored reply (slash command result, failure notice) on an event's
  // own reply route. Never carries model output; keyed once per event+purpose.
  async replyToEvent(event, text, purpose) {
    if (!event?.replyRoute) return { ok: false, stage: 'no-target' };
    const trace = { purpose, connector: event.connectorInstance, action: event.replyRoute.action, inboundSeq: event.seq };
    try {
      const result = await this.deliver({
        caller: { trace },
        connector: event.connectorInstance,
        action: event.replyRoute.action,
        input: { ...event.replyRoute.input, content: { type: 'markdown', text } },
        key: actionKey('daemon', purpose, eventIdentity(event)),
        summary: text,
        scope: { chatId: event.chatId, threadId: event.threadId, convKey: event.conversationKey },
        inboundSeq: event.seq ?? null,
        // Non-chat history is rendered from the event it answers.
        metadata: event.chatId ? {} : { event: event.envelope },
      });
      this.logger.info('daemon_reply', { ...trace, outcome: result.duplicate ? 'duplicate' : result.dry_run ? 'dry_run' : 'ok' });
      return { ok: true };
    } catch (error) {
      this.logger.warn('daemon_reply', { ...trace, outcome: 'failed', error: safeError(error) });
      return { ok: false, stage: 'send', error };
    }
  }

  // A removal a turn's end started is forgotten from temporaryReactions at once,
  // so it is waited for here: the Connectors stop right after.
  async close() {
    await this.clearTemporaryReactions('shutdown');
    await Promise.allSettled([...this.reactionRemovals]);
  }

  // A turn that ended is no longer working on anything, whatever it delivered:
  // its OnIt reactions go too. Each is forgotten at once, before any removal is
  // awaited, so one a later turn sets is never taken for it.
  async clearTemporaryReactions(reason) {
    const scopes = [...this.temporaryReactions.keys()];
    await Promise.allSettled(scopes.map((scope) => this.clearTemporaryReaction(scope, reason)));
  }

  assertAuthority(authority) {
    if (!authority || authority !== this.store.getCognitionState().authorityToken) {
      throw blocked('authority_revoked', `Blocked: this model runtime no longer owns tool authority. ${NO_ACTION}`);
    }
  }

  // Pick the Connector for a target: explicit source, else the connector that
  // owns a known message/chat id, else the only one that supports the operation.
  route(caller, operation, kind, source, { messageId = null, chatId = null } = {}) {
    caller.trace.action = operation;
    const candidates = this.supporting(operation, kind);
    const owner = (messageId ? this.store.connectorForMessage(messageId) ?? this.queryOwner('message', messageId) : null)
      ?? (chatId ? this.store.connectorForChat(chatId) ?? this.queryOwner('chat', chatId) : null);
    const known = source ? this.instanceNamed(source, owner) : owner;
    if (known) {
      if (!candidates.includes(known)) throw blocked('unsupported', `${known} does not support ${operation}. ${NO_ACTION}`);
      caller.trace.connector = known;
      return known;
    }
    if (candidates.length !== 1) {
      throw blocked('route_ambiguous', `Cannot tell which connector this target belongs to; pass source (one of: ${candidates.join(', ')}). ${NO_ACTION}`);
    }
    caller.trace.connector = candidates[0];
    return candidates[0];
  }

  // `source` is an instance, or a connector that runs several (one per
  // mailbox): then the one of them that owns the target, or the only one.
  instanceNamed(source, owner) {
    const manifests = this.connectors.manifests();
    if (manifests.some(([id]) => id === source)) return source;
    const instances = manifests.filter(([, manifest]) => manifest.name === source).map(([id]) => id);
    if (instances.includes(owner)) return owner;
    if (instances.length === 1) return instances[0];
    if (instances.length > 1) {
      throw blocked('route_ambiguous', `${source} has several instances; pass source as one of: ${instances.join(', ')}. ${NO_ACTION}`);
    }
    return source;
  }

  // The accepted inbound events an input reference names: the event= value as
  // its tag showed it, on every instance unless one is given. Callers accept one
  // match and never pick among several.
  inputEvents(ref, instance = null) {
    let id;
    try { id = readMetadataTag(ref); } catch { return []; }
    return this.store.acceptedEvents(id, instance);
  }

  // The one accepted inbound event a decision names. `verb` heads the refusal:
  // a delivery is blocked, a silence is not recorded.
  resolveDecision(input, caller, verb = 'Delivery blocked') {
    const rows = this.inputEvents(input.decision_id, input.decision_instance ?? null);
    if (rows.length === 0) {
      throw blocked('unknown_decision', `${verb}: decision_id is not the event= of an accepted daemon-authenticated input (msg= is not accepted). ${NO_ACTION}`);
    }
    if (rows.length > 1) {
      throw blocked('decision_ambiguous', `${verb}: decision_id is an event on ${rows.length} instances. Pass that line's instance= (or source= when it has none) as decision_instance. ${NO_ACTION}`);
    }
    const [row] = rows;
    Object.assign(caller.trace, { decisionSeq: row.seq, conversationRef: opaqueRef(row.scope_key) });
    caller.convKey = row.conv_key;
    return row;
  }

  // Freshness fence: the decision must resolve to exactly one accepted inbound
  // event with nothing newer accepted in its scope (the chat/thread, or the
  // subject of a non-chat event).
  fence(input, caller) {
    const row = this.resolveDecision(input, caller);
    if (!this.isTimerReminder(row)) this.assertFresh(row, caller);
    return row;
  }

  // A delivery made on a decision settles it in the ledger, even when the send
  // then fails or turns uncertain: the model did decide, and a nudge must not
  // invite a resend. Keyed on the fence, never on the attributed input.
  settleDecision(caller, fence, final) {
    if (!this.config.explicitSilence || !caller.authority || !fence || !final) return;
    try {
      const { created, previous } = this.store.recordDecision({ row: fence, outcome: 'delivered' });
      if (created && previous === 'silent') caller.trace.overrode = 'silent';
    } catch (error) {
      // Bookkeeping only: it must never stop the delivery itself.
      this.logger.warn('decision_record_failed', { error: safeError(error) });
    }
  }

  // stay_silent: the model read the input and chose to deliver nothing. Only a
  // ledger row and an activity: no outbox, journal, connector call or liveness.
  // Nothing about it is ever refused for being repeated or stale.
  async staySilent(input, caller) {
    const row = this.resolveDecision(input, caller, 'Silence not recorded');
    if (this.store.decisionFor(row.connector_instance, row.event_id)?.outcome === 'delivered') {
      return { text: 'A delivery is already recorded for this input; nothing to change. Do not call stay_silent for it.', data: { duplicate: true, delivered: true } };
    }
    const newerSeq = this.store.newerAcceptedSeq(row.scope_key, row.seq);
    const superseded = newerSeq !== null;
    if (superseded) caller.trace.newerSeq = newerSeq;
    caller.trace.superseded = superseded;
    const reason = input.reason ? redactSensitive(input.reason).trim() || null : null;
    const { created } = this.store.recordDecision({ row, outcome: 'silent' });
    if (!created) {
      return { text: 'Already recorded: nothing is sent for this input. Do not call stay_silent again for it.', data: { duplicate: true } };
    }
    // A superseded input's OnIt may belong to work on the newer one.
    if (!superseded && row.conv_key) await this.clearTemporaryReaction(row.conv_key, 'silent');
    try {
      this.store.recordActivity({
        eventKey: `decision:silent:${row.connector_instance}:${row.event_id}`, kind: 'delivery.silent',
        conversationKey: row.conv_key ?? null, inboundSeq: row.seq, status: superseded ? 'SUPERSEDED' : 'SILENT',
        summary: reason ?? '',
        metadata: { tool: 'stay_silent', decisionSeq: row.seq, newerSeq: newerSeq ?? undefined,
          runtime: caller.trace.runtime, via: caller.trace.via },
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind: 'delivery.silent', error: safeError(error) });
    }
    return {
      text: `Recorded: nothing is sent for this input.${superseded ? ' A newer input has arrived there; decide on that one from its own line.' : ''}`,
      data: { silent: true, superseded },
    };
  }

  // Checks the event the decision first resolved to, by its own scope and seq.
  // Looking the alias up again could land on a newer event under the same id
  // and let a stale decision through.
  assertFresh(decision, caller) {
    const newerSeq = this.store.newerAcceptedSeq(decision.scope_key, decision.seq);
    if (newerSeq !== null) {
      caller.trace.newerSeq = newerSeq;
      throw blocked('stale_decision', 'Delivery blocked: a newer authenticated input arrived in the same chat/thread, or about the same subject. Reconsider with the new input, then use its event= as decision_id if the action still fits.');
    }
  }

  // A safety fuse, not a rule the agent works by: whether to answer a bot is
  // the model's call. Bots that keep answering each other would hold the one
  // runtime every chat shares, so with no person writing in a chat/thread,
  // text replies to bot messages there stop at the fuse (0 turns it off) until
  // a person writes. Reactions and notices to the owner cannot loop there.
  assertNoBotLoop(fence, action, scope, caller) {
    const fuse = this.config.botReplyFuse ?? BOT_REPLY_FUSE;
    if (!fuse || !BOT_SENDERS.has(fence.sender_type) || !['message.send', 'message.reply'].includes(action)
      || !fence.conv_key || scope.convKey !== fence.conv_key) return;
    const { person, answered } = this.store.botMessagesAnswered(fence.scope_key, fence.seq);
    if (answered < fuse) return;
    caller.trace.botReplies = answered;
    this.tellOwnerOfFuse(fence, person, answered);
    throw blocked('bot_loop', `Delivery blocked: you have answered ${answered} bot messages in this chat/thread with no person writing there since. Stop answering bots here until a person writes; the owner has been told. ${NO_ACTION}`);
  }

  // The owner hears once per run of bot messages: the key names the person's
  // message the run follows, so a later run is told again.
  tellOwnerOfFuse(fence, person, answered) {
    const connector = this.config.channel;
    const owner = connector ? this.connectors.owner?.(connector) : null;
    if (!owner) return;
    const where = `${fence.connector_instance} 的会话 ${fence.chat_id}${fence.thread_id ? `（线程 ${fence.thread_id}）` : ''}`;
    const text = `${where}里我已经回应了 ${answered} 条 bot 消息，期间一直没有人说话。我先停下，不再回应那里的 bot；有人在那里说一句话就会恢复。`;
    this.deliver({
      caller: { trace: { purpose: 'bot-fuse' } }, connector, action: 'message.send',
      input: { ...owner.target, content: { type: 'markdown', text } },
      key: actionKey('daemon', 'bot-fuse', fence.scope_key, person), summary: text, scope: {},
    }).catch((error) => {
      if (error.code !== 'in_progress') this.logger.warn('bot_fuse_notice_failed', { error: safeError(error) });
    });
  }

  // Attribution is separate from freshness: a response may fulfill an earlier
  // input in the decision's scope, never a newer one. response_to_id resolves on
  // the decision's instance; an implicit target (the message replied or reacted
  // to) counts only when exactly one such event carried it on that connector.
  attribution(caller, explicit, fence, implicit = null) {
    let responseTo = fence;
    const inScope = (row) => row.scope_key === fence.scope_key && row.seq <= fence.seq;
    if (explicit !== undefined) {
      const rows = this.inputEvents(explicit, fence.connector_instance);
      if (rows.length !== 1 || !inScope(rows[0])) {
        throw blocked('attribution_rejected', `Delivery blocked: response_to_id must name one accepted input from the same chat/thread or subject, no newer than decision_id. ${NO_ACTION}`);
      }
      [responseTo] = rows;
    } else if (implicit && implicit.connector === fence.connector_instance) {
      const rows = this.store.acceptedMessageEvents(implicit.messageId, fence.connector_instance);
      if (rows.length === 1 && inScope(rows[0])) [responseTo] = rows;
    }
    caller.trace.responseSeq = responseTo.seq;
    return responseTo;
  }

  // Whether this decision may make this delivery: the one place delivery rules
  // live, so routing rules (a source that may also reach a group, say) can be
  // added here later. Today a non-chat event may only reach the owner: a
  // notice, or a draft the owner approves.
  authorize(fence, { toOwner = false } = {}) {
    if (!fence.conv_key && !toOwner) {
      if (this.isTimerReminder(fence)) return;
      throw blocked('owner_only', `Delivery blocked: a non-chat event can only notify the owner; use notify_owner. ${NO_ACTION}`);
    }
  }

  authorizeTimerDelivery(fence, { connector, action, chatId = null, toOwner = false }) {
    const delivery = timerDelivery(fence);
    if (!delivery) return;
    if (delivery.mode === 'owner') {
      if (toOwner) return;
      throw blocked('timer_delivery_target', `Delivery blocked: this timer occurrence is configured for the owner. Use notify_owner. ${NO_ACTION}`);
    }
    if (delivery.mode === 'chat' && ['message.send', 'message.reply'].includes(action)
      && delivery.source === connector && delivery.chat_id === chatId) return;
    throw blocked('timer_delivery_target', `Delivery blocked: this timer occurrence is configured for ${delivery.source ?? 'unknown'} chat ${delivery.chat_id ?? 'unknown'}. ${NO_ACTION}`);
  }

  isTimerReminder(fence) {
    return fence.connector_instance === 'timer' && fence.kind === 'reminder.due';
  }

  // Where a notice to the owner goes: the primary IM (config.channel), or the
  // IM a chat message named. Picking another IM when the primary is down
  // belongs here too.
  ownerRoute(fence, source, caller) {
    if (source !== undefined && !fence.conv_key) {
      throw blocked('invalid_input', `notify_owner takes source only when a chat message asked to be told on that IM; omit it for a non-chat event. ${NO_ACTION}`);
    }
    const connector = source ?? this.config.channel;
    caller.trace.action = 'message.send';
    caller.trace.connector = connector;
    if (!this.supporting('message.send', 'actions').includes(connector)) {
      throw blocked('unsupported', `${connector} cannot notify the owner. ${NO_ACTION}`);
    }
    const owner = this.connectors.owner?.(connector) ?? null;
    if (!owner) {
      throw blocked('owner_unknown', `Delivery blocked: the owner's ${connector} identity is unknown (for Lark, the daemon profile needs a lark-cli user login). ${NO_ACTION}`);
    }
    return { connector, owner };
  }

  // An interim finding (staged replies) while the work on a request goes on:
  // text, landing in the decision's own conversation (`convKey`: the chat, or
  // the thread there; null when where it lands is unknown). It is not the
  // final delivery, so it records no decision and leaves the temporary OnIt
  // where it is.
  interim(input, caller, fence, connector, convKey) {
    if (input.interim !== true) return false;
    if (input.markdown === undefined) throw blocked('invalid_input', `An interim message is text only: use markdown. ${NO_ACTION}`);
    if (connector !== fence.connector_instance || !convKey || convKey !== fence.conv_key) {
      throw blocked('invalid_input', `An interim message goes only where decision_id is: the same chat, and inside its thread when it has one (reply to its msg=). ${NO_ACTION}`);
    }
    caller.trace.interim = true;
    return true;
  }

  async content(input, connector, action, { chatId, fence, caller, answerer = fence.sender_id }) {
    const spec = this.manifest(connector).actions[action];
    if (input.card_images !== undefined && (!spec.card_images || this.manifest(connector).name !== 'lark')) {
      throw blocked('unsupported', `card_images is supported only by Lark card deliveries. ${NO_ACTION}`);
    }
    let content;
    try {
      content = resolveContent(input, {
        workspace: this.config.workspace,
        tempDir: this.config.tempDir,
        maxTextChars: this.config.maxReplyChars,
      });
    } catch (error) {
      throw blocked('invalid_content', error.message);
    }
    caller.trace.contentType = content.type;
    try {
      if (!spec.content?.includes(content.type)) throw blocked('unsupported', `${connector} cannot send ${content.type} messages. ${NO_ACTION}`);
      const mentions = await this.mentions(input, spec, chatId, connector);
      if (mentions.length > 0) {
        content.payload.mentions = mentions;
        content.canonical = [...content.canonical, mentions];
      }
      if (content.question) {
        content.question.answerer_id ||= answerer;
        if (!/^ou_[A-Za-z0-9_-]{8,}$/.test(content.question.answerer_id ?? '')) {
          throw blocked('invalid_content', 'choices requires a trusted user answerer_id');
        }
      }
      return content;
    } catch (error) {
      content.cleanup();
      throw error;
    }
  }

  async mentions(input, spec, chatId, connector) {
    const ids = input.mention_user_ids ?? [];
    const names = input.mention_names ?? [];
    if (ids.length === 0 && names.length === 0) return [];
    if (!spec.mentions) throw blocked('unsupported', `mentions are not supported on this connector. ${NO_ACTION}`);
    const mentions = new Map(ids.map((id) => [id, { id }]));
    if (names.length > 0 && this.manifest(connector)?.name !== 'lark') {
      // The Connector resolves a name against the people it showed the model there.
      for (const name of names) mentions.set(`name:${name}`, { name });
    } else if (names.length > 0) {
      if (!chatId || !this.roster) {
        throw blocked('invalid_mentions', 'mention_names could not be resolved: the target chat is unknown; use mention_user_ids with full ou_ IDs from trusted metadata');
      }
      let pairs;
      try { pairs = await this.roster.resolveNames(chatId, names); } catch (error) { throw blocked('invalid_mentions', error.message); }
      for (const pair of pairs) mentions.set(pair.id, pair);
    }
    if (mentions.size > 10) throw blocked('invalid_mentions', 'mentions support at most 10 users in total');
    return [...mentions.values()];
  }

  async deliver({
    caller, connector, action, input, key, summary, scope, fence = null, inboundSeq = null, metadata = {}, final = true,
  }) {
    // Preparing the content may have awaited (a mention lookup), and a newer
    // message or a runtime switch could have landed meanwhile. Re-check right
    // before the reservation, with no await in between.
    if (caller.authority) {
      this.assertAuthority(caller.authority);
      if (fence) {
        if (!this.isTimerReminder(fence)) this.assertFresh(fence, caller);
        this.assertNoBotLoop(fence, action, scope, caller);
      }
    }
    if (this.config.dryRun) {
      this.settleDecision(caller, fence, final);
      return { dry_run: true };
    }
    // A Connector that is down and restarting gets no reservation: nothing was
    // sent, and the same call can go through once it is back.
    if (this.connectors.available?.(connector) === false) {
      throw blocked('connector_unavailable',
        `Delivery blocked: the ${connector} connector is temporarily unavailable and reconnects on its own. ${NO_ACTION}`);
    }
    const spec = this.manifest(connector)?.actions[action] ?? {};
    const reservation = this.store.reserveOutbox({
      connector, action, idempotencyKey: key, payload: input, summary,
      caller: caller.authority ? 'agent' : 'daemon', retryUncertain: Boolean(spec.idempotent),
      chatId: scope.chatId ?? null, threadId: scope.threadId ?? null, convKey: scope.convKey ?? null,
      inboundSeq, runtimeToken: caller.authority ?? null, metadata,
    });
    caller.trace.outboxId = reservation.id;
    if (!reservation.claimed) {
      if (reservation.status === 'SENT') {
        this.settleDecision(caller, fence, final);
        return { ...reservation.result, duplicate: true };
      }
      throw reservation.status === 'PENDING'
        ? blocked('in_progress', `Delivery blocked: this identical action is still in progress. ${NO_ACTION}`)
        : blocked('uncertain_not_retried', `Delivery blocked: this identical action was already attempted and its outcome is uncertain. ${NO_ACTION}`);
    }
    this.settleDecision(caller, fence, final);
    caller.trace.attempt = reservation.attempt;
    caller.trace.invoked = true;
    let result;
    try {
      result = await this.connectors.invoke(connector, action, { ...input, idempotency_key: key });
    } catch (error) {
      this.store.settleOutbox(reservation.id, 'UNCERTAIN', { error: safeError(error) });
      throw error;
    }
    this.store.settleOutbox(reservation.id, 'SENT', { result });
    this.liveness?.touch();
    return { ...result, outbox_id: reservation.id };
  }

  // Scope of a target message on its connector: an inbound this daemon
  // accepted, one of its own deliveries, or trusted query routing context.
  messageScope(connector, messageId) {
    return this.store.eventByMessageId(messageId, connector) ?? this.store.deliveredMessage(messageId, connector)
      ?? this.queryTargets.get(JSON.stringify([connector, 'message', messageId]))?.scope ?? null;
  }

  queryOwner(kind, id) {
    const owners = new Set([...this.queryTargets.values()].filter(target => target.kind === kind && target.id === id)
      .map(target => target.connector));
    return owners.size === 1 ? [...owners][0] : null;
  }

  rememberQueryTarget(connector, kind, id, scope = null) {
    const key = JSON.stringify([connector, kind, id]);
    this.queryTargets.delete(key);
    this.queryTargets.set(key, { connector, kind, id, scope });
    while (this.queryTargets.size > 2000) this.queryTargets.delete(this.queryTargets.keys().next().value);
  }

  // Registers a delivered card against where it landed. `seen` are inbounds that
  // may sit in that chat (the replied-to message, the decision); one counts only
  // if it is in the destination chat on the delivering connector.
  rememberCard(content, result, { connector, chatId, seen, replyInThread = false, replyTarget = null }) {
    if (content.type !== 'card' || result.dry_run || result.duplicate) return null;
    const destination = result.chat_id || chatId;
    const inbound = seen.find((row) => row?.connector_instance === connector && row.chat_id === destination) ?? null;
    try {
      registerCard(this.store.db, {
        messageId: result.message_id, profile: this.config.profile, question: content.question ?? null,
        instance: connector, chatId: destination, inbound, chat: inbound ?? this.store.chatContext(connector, destination),
        replyInThread, replyTarget, data: result,
      });
      return null;
    } catch {
      return 'Card was sent, but callback registration failed. Do not resend; card interactions may be unavailable.';
    }
  }

  async sendMessage(input, caller) {
    const connector = this.route(caller, 'message.send', 'actions', input.source, { chatId: input.chat_id });
    const fence = this.fence(input, caller);
    this.authorize(fence);
    this.authorizeTimerDelivery(fence, { connector, action: 'message.send', chatId: input.chat_id });
    // A plain send lands in the chat's main conversation.
    const interim = this.interim(input, caller, fence, connector,
      conversationKeyFor(connector, { tenant_id: fence.tenant_key, id: input.chat_id }));
    const responseTo = this.attribution(caller, input.response_to_id, fence);
    const content = await this.content(input, connector, 'message.send', { chatId: input.chat_id, fence, caller });
    try {
      const result = await this.deliver({
        caller, connector, action: 'message.send',
        input: { chat_id: input.chat_id, content: content.payload },
        key: this.key(input.idempotency_key, 'send', connector, eventRef(fence), eventRef(responseTo), input.chat_id, content.canonical),
        summary: content.summary,
        scope: { chatId: input.chat_id, convKey: responseTo.conv_key },
        fence,
        inboundSeq: responseTo.seq,
        metadata: interim ? { interim: true } : {},
        final: !interim,
      });
      const warning = this.rememberCard(content, result, { connector, chatId: input.chat_id, seen: [fence] });
      if (!result.dry_run && !interim) await this.clearTemporaryReaction(fence.conv_key);
      return this.deliveryResult(result, `message to chat_id=${input.chat_id}`, warning);
    } finally {
      content.cleanup();
    }
  }

  async notifyOwner(input, caller) {
    const fence = this.fence(input, caller);
    const { connector, owner } = this.ownerRoute(fence, input.source, caller);
    this.authorize(fence, { toOwner: true });
    this.authorizeTimerDelivery(fence, { connector, action: 'message.send', toOwner: true });
    const responseTo = this.attribution(caller, input.response_to_id, fence);
    const content = await this.content(input, connector, 'message.send', { chatId: null, fence, caller, answerer: owner.id });
    try {
      const result = await this.deliver({
        caller, connector, action: 'message.send',
        input: { ...owner.target, content: content.payload },
        key: this.key(input.idempotency_key, 'notify', connector, eventRef(fence), eventRef(responseTo), owner.id, content.canonical),
        summary: content.summary,
        scope: {},
        fence,
        inboundSeq: responseTo.seq,
        metadata: { notify: 'owner', decisionEvent: fence.event_id },
      });
      const warning = this.rememberCard(content, result, { connector, chatId: null, seen: [fence] });
      return this.deliveryResult(result, `notification to the owner on ${connector}`, warning);
    } finally {
      content.cleanup();
    }
  }

  async replyMessage(input, caller) {
    const connector = this.route(caller, 'message.reply', 'actions', input.source, { messageId: input.message_id });
    if (input.card_images !== undefined && !this.manifest(connector).actions['message.reply'].card_images) {
      throw blocked('unsupported', `card_images is supported only by Lark card deliveries. ${NO_ACTION}`);
    }
    const inThread = input.reply_in_thread === true;
    if (inThread && !this.manifest(connector).actions['message.reply'].thread) {
      throw blocked('unsupported', `${connector} does not support thread replies. ${NO_ACTION}`);
    }
    const fence = this.fence(input, caller);
    const target = this.messageScope(connector, input.message_id);
    // The reply lands where its target is. A target outside any chat (a mail)
    // has no chat: it never borrows the decision's.
    const chatId = target ? target.chat_id : fence.chat_id;
    const interim = this.interim(input, caller, fence, connector, target?.conv_key ?? null);
    if (this.manifest(connector).actions['message.reply'].approval) return this.proposeReply(input, caller, connector, fence);
    this.authorize(fence);
    this.authorizeTimerDelivery(fence, { connector, action: 'message.reply', chatId });
    const responseTo = this.attribution(caller, input.response_to_id, fence, { connector, messageId: input.message_id });
    const content = await this.content(input, connector, 'message.reply', { chatId, fence, caller });
    try {
      const result = await this.deliver({
        caller, connector, action: 'message.reply',
        input: { message_id: input.message_id, content: content.payload, reply_in_thread: inThread },
        key: this.key(input.idempotency_key, 'reply', connector, eventRef(fence), eventRef(responseTo),
          input.message_id, inThread, content.canonical),
        summary: content.summary,
        scope: { chatId, threadId: target?.thread_id ?? null, convKey: target ? target.conv_key : responseTo.conv_key },
        fence,
        inboundSeq: responseTo.seq,
        // Non-chat history is rendered from the event it answers.
        metadata: target && !target.chat_id && target.event_json ? { event: JSON.parse(target.event_json) }
          : interim ? { interim: true } : {},
        final: !interim,
      });
      const warning = this.rememberCard(content, result, {
        connector, chatId, seen: [this.store.eventByMessageId(input.message_id, connector), fence],
        replyInThread: inThread, replyTarget: input.message_id,
      });
      if (!result.dry_run && !interim) await this.clearTemporaryReaction(fence.conv_key);
      return this.deliveryResult(result, `reply to message_id=${input.message_id}`, warning);
    } finally {
      content.cleanup();
    }
  }

  // A reply the owner must approve (a mail): the draft goes to the owner on a
  // card, never to the target. Any decision may propose one, the mail itself
  // included; the owner's press on that card is what sends it.
  async proposeReply(input, caller, connector, fence) {
    const target = this.store.eventByMessageId(input.message_id, connector);
    if (!target || target.chat_id) {
      throw blocked('invalid_target', `message_id must be a mail this daemon received (its msg= value). ${NO_ACTION}`);
    }
    // The owner answers on their primary IM: a card where it can show one,
    // else a numbered message.
    const { connector: im, owner } = this.ownerRoute(fence, undefined, caller);
    caller.trace.approval = connector;
    const byCard = Boolean(this.manifest(im).actions['message.send'].content?.includes('card'));
    this.authorize(fence, { toOwner: true });
    const responseTo = this.attribution(caller, input.response_to_id, fence, { connector, messageId: input.message_id });
    let draftText;
    try {
      const content = resolveContent(input, { workspace: this.config.workspace, maxTextChars: this.config.maxReplyChars,
        tempDir: this.config.tempDir });
      content.cleanup();
      if (content.type !== 'markdown') throw new Error(`${connector} replies are plain text only; use markdown`);
      draftText = content.payload.text;
    } catch (error) {
      throw blocked('invalid_content', error.message);
    }
    // The draft shows every recipient, and exactly those are sent to.
    const reply = JSON.parse(target.event_json).data?.reply ?? {};
    if (!Array.isArray(reply.to)) {
      throw blocked('unsupported', `This mail asks for replies to go to ${reply.recipients ?? 'more'} addresses, more than an approval draft shows in full, so it cannot be answered here. Tell the owner instead. ${NO_ACTION}`);
    }
    if (reply.to.length === 0) throw blocked('invalid_target', `This mail has no address to reply to. ${NO_ACTION}`);
    const draft = {
      connector, action: 'message.reply', message_id: input.message_id, text: draftText,
      to: reply.to, subject: reply.subject, responseSeq: responseTo.seq,
      ...(typeof reply.from === 'string' ? { from: reply.from } : {}),
    };
    const key = this.key(input.idempotency_key, 'approval', connector, eventRef(fence), input.message_id, draftText);
    const delivery = { caller, connector: im, action: 'message.send', key,
      summary: `[mail reply draft for the owner to approve] ${draftText}`,
      scope: {}, fence, inboundSeq: responseTo.seq, metadata: { notify: 'owner', decisionEvent: fence.event_id } };
    if (!byCard) return this.proposeByMessage({ delivery, draft, im, owner });
    const question = approvalQuestion(draft, owner.id);
    const result = await this.deliver({
      ...delivery,
      input: { ...owner.target, content: { type: 'card', card: approvalCard(draft) } },
    });
    const warning = this.rememberCard({ type: 'card', question }, result, { connector: im, chatId: null, seen: [fence] });
    if (result.dry_run) return this.deliveryResult(result, 'approval card');
    if (warning) return this.deliveryResult(result, 'approval card', `${warning} The reply cannot be approved; draft it again.`);
    return {
      text: result.duplicate
        ? 'This draft is already waiting for the owner. Nothing more to do.'
        : `Nothing was sent yet: the draft is on a card in the owner's direct chat (${im}). The reply goes out only when the owner presses send there, possibly after editing it. Do not send or repeat it another way.`,
      data: result,
    };
  }

  // The draft as a numbered message, for a primary IM without cards. The
  // number is the approval row, made before the message so it can carry it.
  async proposeByMessage({ delivery, draft, im, owner }) {
    const { approval, created } = createApproval(this.store.db, {
      requestKey: delivery.key, im, ownerId: owner.id, draft, ttlMs: APPROVAL_SECONDS * 1_000,
    });
    if (!created) {
      return { text: `This draft is already waiting for the owner as #${approval.id}. Nothing more to do.`, data: { approval: approval.id } };
    }
    let result;
    try {
      result = await this.deliver({
        ...delivery,
        input: { ...owner.target, content: { type: 'markdown', text: approvalMessage(draft, approval.id) } },
      });
    } catch (error) {
      // The owner never saw it, so its number can never be answered.
      if (!delivery.caller.trace.invoked) finishApproval(this.store.db, approval.id, 'failed');
      throw error;
    }
    if (result.dry_run) return this.deliveryResult(result, `approval draft #${approval.id}`);
    supersedeApprovals(this.store.db, { connector: draft.connector, messageId: draft.message_id, by: approval.id });
    return {
      text: `Nothing was sent yet: the draft is waiting in the owner's direct chat (${im}) as #${approval.id}. The mail goes out only when the owner confirms it there, possibly with their own edit; their answer is handled by the daemon and reported to you. If the owner asks for changes, draft it again: the new draft replaces this one. Do not send or repeat it another way.`,
      data: { ...result, approval: approval.id },
    };
  }

  // Sends a draft the owner approved, as `text`. One approval (`key`) sends
  // at most once, whatever was edited.
  async sendApproved({ draft, text, key, trace }) {
    if (text.length > this.config.maxReplyChars) {
      return { state: 'failed', text, note: `超过 ${this.config.maxReplyChars} 字，没有发送。` };
    }
    const mail = this.store.eventByMessageId(draft.message_id, draft.connector);
    try {
      const result = await this.deliver({
        caller: { trace }, connector: draft.connector, action: draft.action,
        input: { message_id: draft.message_id, content: { type: 'markdown', text },
          to: draft.to.map((recipient) => recipient.address) },
        key,
        summary: text,
        scope: {},
        inboundSeq: draft.responseSeq,
        metadata: mail?.event_json ? { event: JSON.parse(mail.event_json) } : {},
      });
      return { state: 'sent', text, duplicate: Boolean(result.duplicate) };
    } catch (error) {
      // Blocked before the Connector was asked: nothing went out.
      return trace.invoked
        ? { state: 'uncertain', text, note: '邮件服务器没有确认收到，可能已经发出，不会自动重发。' }
        : { state: 'failed', text, note: `没有发出：${safeError(error)}` };
    }
  }

  // The owner pressed a button on an approval card: send the draft as they left
  // it, or drop it, then show the outcome on the card. Runs outside the model;
  // the card answer was already accepted once (first press wins).
  async settleApproval(event) {
    const { draft } = event.question;
    const cardId = event.parentId;
    const trace = { purpose: 'approval', connector: draft.connector, action: draft.action, choice: event.answer.key };
    let settled = { state: 'cancelled' };
    if (event.answer.key === 'send') {
      const text = approvedText(draft, event.data.interaction?.form);
      settled = { ...await this.sendApproved({ draft, text, key: actionKey('approved', draft.connector, cardId), trace }),
        edited: text !== draft.text };
    }
    this.logger.info('approval_settled', { ...trace, outcome: settled.state, edited: settled.edited ?? false });
    try {
      await this.connectors.updateCard(cardId, settledApprovalCard(draft, settled));
    } catch (error) {
      this.logger.warn('approval_card_update_failed', { error: safeError(error) });
    }
    return settled;
  }

  // The owner's 发送 N / 不发 N / 修改 N in their direct chat, when N is a
  // draft sent to them on that IM. Anything else is an ordinary message.
  matchApprovalReply(text, event) {
    const reply = parseApprovalReply(text);
    if (!reply) return null;
    const approval = getApproval(this.store.db, reply.number);
    if (!approval || approval.im !== event.connectorInstance || approval.ownerId !== event.senderId) return null;
    return { ...reply, approval };
  }

  // Carries out the owner's answer and tells them what happened. Resolves with
  // the line the model hears about it, or null when there is nothing to tell.
  async answerApproval(event, { verb, number, body }) {
    const db = this.store.db;
    const tell = (text) => this.replyToEvent(event, text, 'approval-answer');
    const status = async () => {
      const approval = getApproval(db, number);
      await tell(approvalStatusText(number, { ...approval, from: approval.draft.from }));
      return null;
    };
    const { draft, edited } = getApproval(db, number);
    const trace = { purpose: 'approval', connector: draft.connector, action: draft.action, choice: verb };
    if (verb === 'edit') {
      if (!body) {
        await tell(`在「修改 ${number}」后面写上新的正文，例如：修改 ${number} 周五下午可以`);
        return null;
      }
      if (body.length > this.config.maxReplyChars) {
        await tell(`新的正文超过 ${this.config.maxReplyChars} 字，没有修改 #${number}。`);
        return null;
      }
      const updated = editApproval(db, number, body);
      if (!updated) return status();
      this.logger.info('approval_edited', trace);
      await tell(approvalMessage(updated.draft, number, { edited: true }));
      return null;
    }
    if (verb === 'cancel') {
      if (!cancelApproval(db, number)) return status();
      this.logger.info('approval_settled', { ...trace, outcome: 'cancelled' });
      await tell(approvalStatusText(number, { state: 'cancelled' }));
      return approvalNote(draft, { state: 'cancelled' });
    }
    if (!claimApproval(db, number)) return status();
    const settled = { ...await this.sendApproved({ draft, text: draft.text,
      key: actionKey('approved', draft.connector, `approval-${number}`), trace }), edited };
    finishApproval(db, number, settled.state);
    this.logger.info('approval_settled', { ...trace, outcome: settled.state, edited });
    await tell(approvalStatusText(number, { ...settled, from: draft.from }));
    return approvalNote(draft, settled);
  }

  async reactToMessage(input, caller) {
    const connector = this.route(caller, 'message.react', 'actions', input.source, { messageId: input.message_id });
    const temporary = input.temporary === true;
    if (temporary && input.emoji_type !== 'OnIt') throw blocked('invalid_input', 'temporary reactions must use the OnIt emoji');
    const fence = this.fence(input, caller);
    this.authorize(fence);
    if (temporary && !fence.conv_key) throw blocked('invalid_input', 'temporary reaction scope could not be verified');
    const target = this.messageScope(connector, input.message_id);
    const responseTo = this.attribution(caller, input.response_to_id, fence, { connector, messageId: input.message_id });
    const result = await this.deliver({
      caller, connector, action: 'message.react',
      input: { message_id: input.message_id, emoji_type: input.emoji_type },
      key: this.key(null, 'reaction', connector, eventRef(fence), eventRef(responseTo), input.message_id, input.emoji_type, temporary),
      summary: `[reaction ${input.emoji_type}]`,
      scope: { chatId: target?.chat_id ?? fence.chat_id, threadId: target?.thread_id ?? null, convKey: responseTo.conv_key },
      fence,
      inboundSeq: responseTo.seq,
      // A temporary OnIt says "working", not a decision.
      final: !temporary,
    });
    if (!result.dry_run && !result.duplicate) {
      if (temporary && result.reaction_id) {
        await this.rememberTemporaryReaction(fence.conv_key, {
          connector, messageId: input.message_id, reactionId: result.reaction_id, outboxId: result.outbox_id,
        });
      } else if (!temporary) {
        await this.clearTemporaryReaction(fence.conv_key);
      }
    }
    return this.deliveryResult(result, `${input.emoji_type} reaction on message_id=${input.message_id}`);
  }

  async retractMessage(input, caller) {
    const connector = this.route(caller, 'message.retract', 'actions', input.source, { messageId: input.message_id });
    // decision_id is offered only with explicit silence on: the retraction is
    // then fenced and recorded like any other final delivery.
    const fence = input.decision_id === undefined ? null : this.fence(input, caller);
    const target = this.store.deliveredMessage(input.message_id, connector);
    const result = await this.deliver({
      caller, connector, action: 'message.retract',
      input: { message_id: input.message_id },
      key: this.key(null, 'retract', connector, input.message_id),
      summary: '[message retracted]',
      scope: { chatId: target?.chat_id ?? null, convKey: target?.conv_key ?? null },
      fence,
      inboundSeq: fence?.seq ?? null,
    });
    return this.deliveryResult(result, `retraction of message_id=${input.message_id}`);
  }

  key(requested, ...parts) {
    const canonical = actionKey(...parts);
    // A caller label is bound to the payload, so reusing one label for two
    // different messages cannot make the provider collapse the second.
    return requested ? actionKey('caller', requested, canonical) : canonical;
  }

  deliveryResult(result, what, warning = null) {
    if (result.dry_run) return { text: `Dry run only: no ${what} was delivered.`, data: result };
    const remote = result.message_id ? ` message_id=${result.message_id}` : '';
    const text = result.duplicate
      ? `Already delivered earlier: ${what}.${remote} No new message was sent.`
      : `Delivered ${what}.${remote}${warning ? ` Warning: ${warning}` : ''}`;
    return { text, data: { ...result, ...(warning ? { delivery_warning: warning } : {}) } };
  }

  async rememberTemporaryReaction(scope, token) {
    const previous = this.temporaryReactions.get(scope);
    this.temporaryReactions.set(scope, token);
    this.logger.info('temporary_reaction', { event: 'tracked', conversationRef: opaqueRef(scope), outboxId: token.outboxId });
    if (previous) await this.deleteTemporaryReaction(previous, scope, 'replaced');
  }

  async clearTemporaryReaction(scope, reason = 'final_delivery') {
    const token = scope ? this.temporaryReactions.get(scope) : null;
    if (!token) return;
    this.temporaryReactions.delete(scope);
    await this.deleteTemporaryReaction(token, scope, reason);
  }

  async deleteTemporaryReaction(token, scope, reason) {
    const removal = this.removeTemporaryReaction(token, scope, reason);
    this.reactionRemovals.add(removal);
    try {
      await removal;
    } finally {
      this.reactionRemovals.delete(removal);
    }
  }

  // Compensating cleanup of an outbox-recorded reaction. Best-effort: the final
  // delivery that triggered it already succeeded and must not be reported failed.
  async removeTemporaryReaction(token, scope, reason) {
    const fields = { conversationRef: opaqueRef(scope), outboxId: token.outboxId, reason };
    try {
      await this.connectors.invoke(token.connector, 'message.reaction.delete', {
        message_id: token.messageId, reaction_id: token.reactionId,
      });
      this.store.cancelOutboxJournal(token.outboxId);
      this.logger.info('temporary_reaction', { event: 'cleared', ...fields });
    } catch (error) {
      this.logger.warn('temporary_reaction', { event: 'cleanup_failed', ...fields, error: safeError(error) });
    }
  }

  async query(caller, operation, { source, ...input }, ids = {}) {
    const connector = this.route(caller, operation, 'queries', source, ids);
    if (input.cursor !== undefined && this.manifest(connector).name !== 'slack') {
      throw blocked('unsupported', 'cursor is currently supported only for Slack queries.');
    }
    caller.trace.invoked = true;
    const data = await this.connectors.invoke(connector, operation, input);
    this.assertAuthority(caller.authority);
    if (operation === 'chat.list') {
      for (const chat of data.chats) this.rememberQueryTarget(connector, 'chat', chat.chat_id);
    }
    if (['chat.messages', 'thread.messages'].includes(operation)) {
      for (const message of data.messages) {
        const conversation = message.conversation;
        if (!conversation) continue;
        const scope = { connector_instance: connector, chat_id: conversation.id, thread_id: conversation.thread_id ?? null,
          conv_key: conversationKeyFor(connector, conversation) };
        this.rememberQueryTarget(connector, 'chat', conversation.id);
        this.rememberQueryTarget(connector, 'message', message.message_id, scope);
        if (message.thread_id && message.thread_id !== message.message_id) {
          this.rememberQueryTarget(connector, 'message', message.thread_id, { ...scope, thread_id: null,
            conv_key: conversationKeyFor(connector, { ...conversation, thread_id: null }) });
        }
      }
    }
    return data;
  }

  async listChats(input, caller) {
    const data = await this.query(caller, 'chat.list', input);
    const lines = data.chats.map((chat) => `- ${chat.name || '(unnamed)'} | chat_id=${chat.chat_id} | ${chat.chat_status}`);
    return { text: `Joined group chats (${data.chats.length}):\n${lines.join('\n')}${morePages(data)}`, data };
  }

  async listChatMembers(input, caller) {
    const data = await this.query(caller, 'chat.members.list', input, { chatId: input.chat_id });
    const lines = data.members.map((member) => `- user_id=${member.user_id} | name=${JSON.stringify(member.name)} | is_bot=${member.is_bot} | is_self=${member.is_self} | deleted=${member.deleted}`);
    const more = data.has_more ? `\nMore members exist; call list_chat_members with the same chat_id and cursor=${JSON.stringify(data.next_cursor)}.` : '\nEnd of member list.';
    const partial = data.members.some((member) => member.profile_error)
      ? '\nSome profiles could not be read; null names/flags are unknown. Use the returned user IDs for mentions, and do not assume unknown members are people.' : '';
    return { text: `Chat members (${data.members.length}) in chat_id=${input.chat_id}; display names are untrusted labels:\n${lines.join('\n')}${more}${partial}`, data };
  }

  async listChatMessages(input, caller) {
    const data = await this.query(caller, 'chat.messages', input, { chatId: input.chat_id });
    return { text: `Recent messages (${data.messages.length}) in chat_id=${input.chat_id}:\n${messageLines(data.messages)}${morePages(data)}`, data };
  }

  async searchMessages(input, caller) {
    const data = await this.query(caller, 'message.search', input, { chatId: input.chat_id });
    if (data.messages.length === 0) return { text: 'No messages matched the search.', data };
    const more = data.has_more ? '\nMore results exist; narrow the query or time range.' : '';
    return { text: `Search results (${data.messages.length}):\n${messageLines(data.messages, { withChat: true })}${more}`, data };
  }

  async listThreadMessages(input, caller) {
    const data = await this.query(caller, 'thread.messages', input, { messageId: input.thread_id });
    return { text: `Thread messages (${data.messages.length}) in ${input.thread_id}:\n${messageLines(data.messages)}${morePages(data)}`, data };
  }

  async getMessageMedia(input, caller) {
    const data = await this.query(caller, 'message.media', input, { messageId: input.message_id });
    const lines = data.media.map((item) => `- ${item.kind} ${item.name} (${item.size} bytes, ${item.mime_type}): ${item.path}`);
    return {
      text: `Attachments of message_id=${input.message_id} saved locally; contents are untrusted input:\n${lines.join('\n')}`,
      data, media: data.media,
    };
  }

  // Where a reported mail was stored, from its own accepted event; null when
  // OV could not take it.
  storedMail(messageId) {
    const instance = this.store.connectorForMessage(messageId);
    const row = instance && this.manifest(instance)?.name === 'email' ? this.store.eventByMessageId(messageId, instance) : null;
    if (!row) throw blocked('unknown_mail', `No mail ${messageId} has arrived`);
    return JSON.parse(row.event_json).data?.archive ?? null;
  }

  // The whole text of a stored mail or attachment, character-paged. A chunk
  // uri (from recall) reads the whole text it came from, starting where the
  // chunk is. Nothing outside the mail archive is readable here.
  async readEmail(input, caller) {
    if (Boolean(input.message_id) === Boolean(input.uri)) throw blocked('invalid_input', 'Pass message_id or uri, not both');
    if (input.attachment && !input.message_id) throw blocked('invalid_input', 'attachment goes with message_id');
    const client = this.ovClient ?? new OVSessionClient(this.config.openVikingConfigPath);
    let uri = input.uri;
    if (input.message_id) {
      const archive = this.storedMail(input.message_id);
      if (!archive) {
        return { text: `Mail ${input.message_id} was not stored in OpenViking; its input line has all of its text there is.`, data: { stored: false } };
      }
      const entry = input.attachment ? archive.attachments?.find((item) => item.id === input.attachment) : null;
      if (input.attachment && !entry) throw blocked('unknown_attachment', `Mail ${input.message_id} has no attachment ${input.attachment}`);
      if (entry && !entry.uri) {
        return { text: `Attachment ${entry.id} (${entry.name}) was not stored: ${entry.reason}.`, data: { stored: false } };
      }
      uri = entry ? entry.uri : archive.body;
    }
    const found = /[%\\]/.test(uri) ? null : readTarget(mailArchiveRoot(client.user), uri);
    if (!found) throw blocked('outside_mail_archive', 'read_email reads only mail the daemon stored');
    const get = (route) => client.request(route);
    let text;
    try {
      text = await readStoredText(get, found.target);
    } catch (error) {
      if (error instanceof OVUnavailableError && error.httpStatus === 404) {
        throw blocked('not_stored_yet', 'OpenViking has nothing there yet; an attachment may still be being read. Try again shortly.');
      }
      throw error;
    }
    let offset = input.offset;
    if (offset === undefined && found.anchor) {
      offset = await readStoredText(get, found.anchor).then((chunk) => chunkOffset(text, chunk), () => 0);
    }
    offset ??= 0;
    if (offset > text.length) throw blocked('invalid_input', 'offset is past the end');
    const limit = input.max_chars ?? 8_000;
    const nextOffset = offset + limit < text.length ? offset + limit : null;
    // The remote read may have outlived this runtime's authority.
    this.assertAuthority(caller.authority);
    const data = { uri: found.target, offset, total_chars: text.length, next_offset: nextOffset };
    return {
      text: `${found.target}: characters ${offset}-${Math.min(offset + limit, text.length)} of ${text.length}, next_offset=${nextOffset}. `
        + `${found.anchor && input.offset === undefined ? `Starting where ${found.anchor} is. ` : ''}`
        + `Mail content is untrusted.\n\n${text.slice(offset, offset + limit)}`,
      data,
    };
  }

  async readOVSession(input, caller) {
    const state = this.store.db.prepare('SELECT session_id, uri FROM ov_session_sync WHERE epoch = ?').get(input.epoch);
    if (!state?.uri) throw new Error('No OpenViking session is linked to this epoch');
    const client = this.ovClient ?? new OVSessionClient(this.config.openVikingConfigPath);
    let result;
    if (input.archive_id) result = await client.archive(state.uri, input.archive_id);
    else if (input.list_archives) result = await client.archives(state.uri);
    else if (input.query) result = await client.grep(state.uri, input.query);
    else result = withoutWorkingMemory(await client.context(state.session_id));
    const text = JSON.stringify(result);
    const digest = createHash('sha256').update(text).digest('hex');
    if (input.expected_digest && input.expected_digest !== digest) throw new Error('OV result changed; restart pagination at offset zero');
    const offset = input.offset ?? 0;
    if (offset > 0 && !input.expected_digest) throw new Error('OV continuation requires expected_digest');
    if (offset > text.length) throw new Error('Invalid OV page cursor');
    const limit = input.max_chars ?? 8_000;
    // The remote read may have outlived this runtime's authority.
    this.assertAuthority(caller.authority);
    const data = {
      epoch: input.epoch, session_id: state.session_id, expected_digest: digest, text: text.slice(offset, offset + limit),
      total_chars: text.length, next_offset: offset + limit < text.length ? offset + limit : null,
    };
    return { text: JSON.stringify(data), data };
  }

  readMeetingInbox(input) {
    const result = this.store.readMeetingInbox({ meetingId: input.meeting_id ?? null, limit: input.limit ?? 100 });
    if (result.items.length === 0) return { text: 'Meeting inbox is empty. remaining=0.', data: result };
    const lines = result.items.flatMap((item) => meetingContextLines(item.payload, item.activityType)
      .map((line) => `- meeting=${item.meetingId} time=${new Date(item.occurredAtMs).toISOString()} ${line}`));
    return {
      text: `Read and acknowledged ${result.items.length} meeting messages; remaining=${result.remaining}.\n${lines.join('\n')}`,
      data: result,
    };
  }

  getTime() {
    const nowMs = this.clock();
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const data = {
      now_ms: nowMs,
      now: new Date(nowMs).toISOString(),
      timezone,
    };
    return { text: JSON.stringify(data), data };
  }

  normalizeDelivery(delivery) {
    const value = delivery && typeof delivery === 'object' && !Array.isArray(delivery) ? delivery : null;
    if (!value) throw blocked('invalid_input', 'delivery must be an object');
    if (value.mode === 'owner') return { mode: 'owner' };
    if (value.mode === 'chat' && typeof value.source === 'string' && typeof value.chat_id === 'string' && value.chat_id) {
      return { mode: 'chat', source: value.source, chat_id: value.chat_id };
    }
    throw blocked('invalid_input', 'delivery must be {mode:"owner"} or {mode:"chat", source, chat_id}');
  }

  async createTimer(input, caller) {
    if (!this.timers) throw blocked('unknown_tool', 'timer tools are not available');
    const fence = this.fence(input, caller);
    const existing = this.timers.findByDecision(fence.connector_instance, fence.event_id);
    if (existing) return this.timerResult(existing, { duplicate: true });
    const maxRuns = input.max_runs === undefined || input.max_runs === null ? null : Number(input.max_runs);
    if (maxRuns !== null && (!Number.isSafeInteger(maxRuns) || maxRuns < 1)) {
      throw blocked('invalid_input', 'max_runs must be a positive integer or null');
    }
    const nowMs = this.clock();
    const timezone = input.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const normalized = TimerSchedule.normalize(input.schedule, {
      nowMs,
      timezone,
    });
    const timer = this.timers.create({
      title: input.title,
      text: input.text,
      timezone,
      schedule: normalized.schedule,
      nextRunAtMs: normalized.nextRunAtMs,
      maxRuns,
      delivery: this.normalizeDelivery(input.delivery),
      createdFromEventId: fence.event_id,
      createdFromSource: fence.connector_instance,
      createdAtMs: nowMs,
    });
    this.onTimersChanged?.();
    return this.timerResult(timer, { duplicate: false });
  }

  timerResult(timer, { duplicate = false } = {}) {
    const data = {
      timer_id: timer.timerId,
      title: timer.title,
      text: timer.text,
      timezone: timer.timezone,
      next_run_at_ms: timer.nextRunAtMs,
      schedule: timer.schedule,
      max_runs: timer.maxRuns,
      delivery: timer.delivery,
      run_count: timer.runCount,
      duplicate,
    };
    return {
      text: duplicate
        ? `Timer already exists: timer_id=${timer.timerId}.`
        : `Timer created: timer_id=${timer.timerId}.`,
      data,
    };
  }

  listTimers() {
    if (!this.timers) throw blocked('unknown_tool', 'timer tools are not available');
    const timers = this.timers.listActive().map((timer) => ({
      timer_id: timer.timerId,
      title: timer.title,
      text: timer.text,
      timezone: timer.timezone,
      next_run_at_ms: timer.nextRunAtMs,
      schedule: timer.schedule,
      max_runs: timer.maxRuns,
      delivery: timer.delivery,
      run_count: timer.runCount,
    }));
    if (timers.length === 0) return { text: 'No active timers.', data: { timers } };
    const lines = timers.map((timer) => `- ${timer.timer_id} | ${timer.title} | next_run_at_ms=${timer.next_run_at_ms}`);
    return { text: `Active timers (${timers.length}):\n${lines.join('\n')}`, data: { timers } };
  }

  cancelTimer(input) {
    if (!this.timers) throw blocked('unknown_tool', 'timer tools are not available');
    const cancelled = this.timers.cancelWithOccurrenceChecks(input.timer_id);
    if (cancelled) this.onTimersChanged?.();
    return {
      text: cancelled ? `Timer cancelled: timer_id=${input.timer_id}.` : `Timer was not active: timer_id=${input.timer_id}.`,
      data: { timer_id: input.timer_id, cancelled },
    };
  }

  completeTimerOccurrence(input) {
    if (!this.timers) throw blocked('unknown_tool', 'timer tools are not available');
    if (!this.store.sentTimerOccurrenceDelivery(input.occurrence_id)) {
      return {
        text: `Timer occurrence is not complete: no successful delivery is recorded for occurrence_id=${input.occurrence_id}.`,
        data: { occurrence_id: input.occurrence_id, completed: false },
      };
    }
    const check = this.timers.markOccurrenceCompleted(input.occurrence_id);
    if (!check) {
      return {
        text: `Timer occurrence check was not pending: occurrence_id=${input.occurrence_id}.`,
        data: { occurrence_id: input.occurrence_id, completed: false },
      };
    }
    return {
      text: `Timer occurrence completed: occurrence_id=${input.occurrence_id}.`,
      data: { occurrence_id: input.occurrence_id, completed: true, status: check.status },
    };
  }
}
