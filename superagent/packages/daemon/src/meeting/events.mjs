// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

export const MEETING_EVENT_TYPES = Object.freeze([
  'vc.bot.meeting_invited_v1',
  'vc.bot.meeting_activity_v1',
  'vc.bot.meeting_ended_v1',
]);

const MEETING_EVENT_SET = new Set(MEETING_EVENT_TYPES);

export function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

export function namedValue(value, names, depth = 0) {
  if (depth > 8 || !value || typeof value !== 'object') return null;
  for (const [key, child] of Object.entries(value)) {
    if (names.has(key) && child !== undefined && child !== null && child !== '') return child;
  }
  for (const child of Object.values(value)) {
    const found = namedValue(child, names, depth + 1);
    if (found !== null) return found;
  }
  return null;
}

export function textValue(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number' || typeof value === 'bigint') return String(value);
  return null;
}

export function timestampMs(value, fallback = Date.now()) {
  const source = String(value ?? '').trim();
  if (!source) return fallback;
  if (/^\d+$/.test(source)) {
    const number = Number.parseInt(source, 10);
    if (Number.isFinite(number)) return number < 10_000_000_000 ? number * 1_000 : number;
  }
  const parsed = Date.parse(source);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function eventTypeOf(input) {
  return textValue(input?.header?.event_type ?? input?.event_type
    ?? input?.data?.event_type ?? input?.data?.event?.event_type ?? input?.type);
}

export function isMeetingEvent(input) {
  return MEETING_EVENT_SET.has(eventTypeOf(input));
}

export function normalizeMeetingEvent(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('meeting event must be a JSON object');
  }
  const header = object(input.header ?? input.data?.header);
  const event = object(input.event ?? input.data?.event ?? input.data ?? input);
  const eventType = eventTypeOf(input);
  if (!MEETING_EVENT_SET.has(eventType)) throw new Error(`unsupported meeting event type: ${eventType ?? 'missing'}`);
  const eventId = textValue(header.event_id ?? input.event_id)
    ?? createHash('sha256').update(JSON.stringify(input)).digest('hex');
  const activityItems = Array.isArray(event.meeting_activity_items)
    ? event.meeting_activity_items
    : Array.isArray(event.meeting_actitivty_items) ? event.meeting_actitivty_items : [];
  const primaryActivity = object(activityItems[0]);
  const meeting = object(namedValue(primaryActivity, new Set(['meeting']))
    ?? namedValue(event, new Set(['meeting'])));
  const meetingId = textValue(namedValue(event, new Set(['meeting_id', 'meetingId'])) ?? meeting.id);
  const meetingNumber = textValue(namedValue(event, new Set([
    'meeting_no', 'meeting_number', 'meetingNo', 'meetingNumber',
  ])));
  const callId = textValue(namedValue(event, new Set(['call_id', 'callId'])));
  const activityType = textValue(namedValue(primaryActivity, new Set([
    'activity_event_type', 'activity_type', 'activityEventType', 'event_type',
  ])) ?? namedValue(event, new Set([
    'activity_event_type', 'activity_type', 'activityEventType', 'event_type',
  ]))) ?? eventType;
  const sequenceRaw = namedValue(primaryActivity, new Set(['event_seq', 'activity_seq', 'sequence']))
    ?? namedValue(event, new Set(['event_seq', 'activity_seq', 'sequence']));
  const sequence = Number.parseInt(String(sequenceRaw ?? ''), 10);
  return {
    eventType,
    eventId,
    tenantKey: textValue(header.tenant_key ?? input.tenant_key),
    createTimeMs: timestampMs(header.create_time ?? input.create_time ?? input.timestamp),
    meetingId,
    meetingNumber,
    callId,
    activityType,
    sequence: Number.isSafeInteger(sequence) && sequence >= 0 ? sequence : null,
    pageToken: textValue(namedValue(event, new Set(['page_token', 'pageToken']))),
    recoveryRequested: namedValue(event, new Set(['need_recovery', 'recovery_required', 'has_more'])) === true,
    activityItems,
    raw: input,
  };
}

export function arraysNamed(value, names, output = [], depth = 0) {
  if (depth > 10 || !value || typeof value !== 'object') return output;
  for (const [key, child] of Object.entries(value)) {
    if (names.has(key) && Array.isArray(child)) output.push(...child);
    else arraysNamed(child, names, output, depth + 1);
  }
  return output;
}

export function firstText(value, names) {
  return textValue(namedValue(value, new Set(names)));
}

function cleanLine(value) {
  return String(value ?? '').replace(/\s*[\r\n\u2028\u2029]+\s*/g, ' ⏎ ')
    .replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
}

function actorLabel(value, fallback = 'unknown') {
  return firstText(value, [
    'speaker_name', 'participant_name', 'display_name', 'user_name', 'label', 'name',
  ]) ?? fallback;
}

export function meetingContextLines(payload, activityType = 'meeting_activity') {
  const transcriptItems = arraysNamed(payload, new Set(['transcript_received_items', 'transcript_items']));
  const chatItems = arraysNamed(payload, new Set(['chat_received_items', 'chat_items']));
  const shareItems = arraysNamed(payload, new Set(['magic_share_started_items', 'share_started_items']));
  const outerActor = actorLabel(payload);
  const lines = [];
  for (const item of transcriptItems) {
    const content = firstText(item, ['content', 'text', 'transcript', 'sentence']);
    if (content) lines.push(`[transcript] ${actorLabel(item, outerActor)}: ${cleanLine(content)}`);
  }
  for (const item of chatItems) {
    const content = firstText(item, ['content', 'text', 'message']);
    if (content) lines.push(`[meeting chat] ${actorLabel(item, outerActor)}: ${cleanLine(content)}`);
  }
  for (const item of shareItems) {
    const title = firstText(item, ['title', 'name']) ?? 'shared content';
    lines.push(`[meeting share] ${actorLabel(item, outerActor)} shared ${cleanLine(title)}`);
  }
  if (lines.length === 0 && /transcript/i.test(String(activityType))) {
    const content = firstText(payload, ['content', 'text', 'transcript', 'sentence']);
    if (content) lines.push(`[transcript] ${outerActor}: ${cleanLine(content)}`);
  }
  if (lines.length === 0) lines.push(`[meeting activity] ${cleanLine(activityType)}`);
  return lines;
}

function validClosedTimestamp(value) {
  const source = String(value ?? '').trim();
  if (!source) return false;
  if (/^\d+$/.test(source)) return Number.parseInt(source, 10) > 0;
  return Number.isFinite(Date.parse(source));
}

export function meetingActivityDebounceMs(
  payload,
  activityType = 'meeting_activity',
  { stableMs = 1_000, unstableMs = 3_000 } = {},
) {
  const transcripts = arraysNamed(payload, new Set(['transcript_received_items', 'transcript_items']));
  if (transcripts.length > 0 || /transcript/i.test(String(activityType))) {
    const closed = transcripts.length > 0 && transcripts.every((item) => validClosedTimestamp(
      namedValue(item, new Set(['end_time_ms', 'endTimeMs', 'end_time', 'endTime'])),
    ));
    return closed ? stableMs : unstableMs;
  }
  return stableMs;
}

function semanticIdentity(payload, activityType) {
  const groups = [
    ['transcript', ['transcript_received_items', 'transcript_items'], ['sentence_id', 'sentenceId'], ['content', 'text', 'transcript', 'sentence']],
    ['chat', ['chat_received_items', 'chat_items'], ['message_id', 'messageId'], ['content', 'text', 'message']],
    ['share_started', ['magic_share_started_items', 'share_started_items'], ['share_id', 'shareId'], ['title', 'name', 'url']],
    ['share_ended', ['magic_share_ended_items', 'share_ended_items'], ['share_id', 'shareId'], ['title', 'name', 'url']],
    ['participant_joined', ['participant_joined_items'], ['id'], ['user_name', 'name']],
    ['participant_left', ['participant_left_items'], ['id'], ['user_name', 'name']],
  ];
  const values = [];
  for (const [kind, keys, idKeys, contentKeys] of groups) {
    for (const item of arraysNamed(payload, new Set(keys))) {
      values.push({
        kind,
        id: firstText(item, idKeys),
        content: cleanLine(firstText(item, contentKeys) ?? ''),
      });
    }
  }
  return values.length > 0 ? values : [{ kind: String(activityType), content: cleanLine(meetingContextLines(payload, activityType).join('\n')) }];
}

export function meetingEventFingerprint(payload, activityType = 'meeting_activity') {
  return createHash('sha256').update(JSON.stringify(semanticIdentity(payload, activityType))).digest('hex');
}

export function semanticFragments(item, meetingId, activityType, fallbackAtMs) {
  const specs = [
    { kind: 'transcript', keys: ['transcript_received_items', 'transcript_items'], outputKey: 'transcript_received_items', idKeys: ['sentence_id', 'sentenceId'], timeKeys: ['end_time_ms', 'start_time_ms'] },
    { kind: 'chat', keys: ['chat_received_items', 'chat_items'], outputKey: 'chat_received_items', idKeys: ['message_id', 'messageId'], timeKeys: ['send_time', 'time'] },
    { kind: 'share_started', keys: ['magic_share_started_items', 'share_started_items'], outputKey: 'magic_share_started_items', idKeys: ['share_id', 'shareId'], timeKeys: ['time'] },
    { kind: 'share_ended', keys: ['magic_share_ended_items', 'share_ended_items'], outputKey: 'magic_share_ended_items', idKeys: ['share_id', 'shareId'], timeKeys: ['time'] },
    { kind: 'participant_joined', keys: ['participant_joined_items'], outputKey: 'participant_joined_items', idKeys: ['join_time'], timeKeys: ['join_time'] },
    { kind: 'participant_left', keys: ['participant_left_items'], outputKey: 'participant_left_items', idKeys: ['leave_time'], timeKeys: ['leave_time'] },
  ];
  const fragments = [];
  for (const spec of specs) {
    for (const child of arraysNamed(item, new Set(spec.keys))) {
      const providerId = firstText(child, spec.idKeys);
      const participantId = spec.kind.startsWith('participant_') ? firstText(child, ['id']) : null;
      const stableId = participantId && providerId ? `${participantId}:${providerId}`
        : providerId ?? createHash('sha256').update(JSON.stringify(child)).digest('hex');
      const key = createHash('sha256').update(`${meetingId}\0${spec.kind}\0${stableId}`).digest('hex');
      fragments.push({
        kind: spec.kind,
        eventKey: `meeting:${key}`,
        occurredAtMs: timestampMs(namedValue(child, new Set(spec.timeKeys)), fallbackAtMs),
        payload: {
          activity_event_type: activityType,
          ...(Array.isArray(item.actors) ? { actors: item.actors } : {}),
          [spec.outputKey]: [child],
        },
      });
    }
  }
  if (fragments.length > 0) return fragments;
  const digest = createHash('sha256').update(`${meetingId}\0${activityType}\0${JSON.stringify(item)}`).digest('hex');
  return [{
    kind: 'activity',
    eventKey: `meeting:${digest}`,
    occurredAtMs: fallbackAtMs,
    payload: item,
  }];
}

export function joinMeetingFrom(value) {
  const meeting = value?.data?.meeting ?? value?.meeting ?? value?.data?.data?.meeting;
  return {
    meetingId: textValue(meeting?.id ?? meeting?.meeting_id ?? namedValue(value, new Set(['meeting_id', 'meetingId']))),
    meetingNumber: textValue(meeting?.meeting_no ?? meeting?.meeting_number),
  };
}

export function pullEnvelope(value) {
  const data = value?.data?.data ?? value?.data ?? value ?? {};
  return {
    events: Array.isArray(data.events) ? data.events : Array.isArray(data.items) ? data.items : [],
    pageToken: textValue(data.page_token ?? data.pageToken ?? value?.page_token),
    hasMore: Boolean(data.has_more ?? data.hasMore),
  };
}
