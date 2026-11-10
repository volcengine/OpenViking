// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { opaqueRef, redactSensitive } from '../logger.mjs';
import {
  firstText,
  joinMeetingFrom,
  meetingActivityDebounceMs,
  meetingContextLines,
  namedValue,
  object,
  pullEnvelope,
  semanticFragments,
  textValue,
  timestampMs,
} from './events.mjs';

const safe = (error) => redactSensitive(error instanceof Error ? error.message : String(error));

export class MeetingAdapter {
  constructor({
    store,
    lark,
    logger,
    batchDelayMs = 1_000,
    batchMaxDelayMs = 4_000,
    batchMaxEvents = 50,
    batchMaxChars = 30_000,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
    now = Date.now,
    onInboxReady = null,
  }) {
    Object.assign(this, {
      store,
      lark,
      logger,
      batchDelayMs,
      batchMaxEvents,
      batchMaxChars,
      setTimer,
      clearTimer,
      now,
      onInboxReady,
    });
    this.batchMaxDelayMs = Math.max(batchDelayMs, batchMaxDelayMs);
    this.timers = new Map();
    this.batchWindows = new Map();
    this.ingress = Promise.resolve();
    this.stopped = false;
  }

  handleEvent(input) {
    const work = this.ingress.then(() => this._handleEvent(input));
    this.ingress = work.catch((error) => {
      this.logger.warn('meeting_event_failed', { error: safe(error) });
    });
    return work;
  }

  async _handleEvent(input) {
    if (this.stopped) return false;
    const event = input;
    if (event.eventType === 'vc.bot.meeting_invited_v1') return this._handleInvite(event);
    if (event.eventType === 'vc.bot.meeting_activity_v1' && event.activityItems.length) {
      return this._handleActivityItems(event);
    }
    if (!event.meetingId) throw new Error(`${event.eventType} is missing meeting_id`);

    const before = this.store.ensureMeetingSession(event.meetingId, {
      meetingNumber: event.meetingNumber,
      atMs: event.createTimeMs,
    });
    const inserted = this.store.ingestMeetingEvent({
      eventKey: `push:${event.eventId}`,
      meetingId: event.meetingId,
      eventType: event.eventType,
      activityType: event.activityType,
      occurredAtMs: event.createTimeMs,
      payload: event,
    });
    const gap = this.store.meetingHasSequenceGap(event.meetingId, event.sequence);
    this.store.updateMeetingProgress(event.meetingId, {
      pageToken: event.pageToken,
      sequence: event.sequence,
      eventAtMs: event.createTimeMs,
    });
    if (inserted) this._schedule(event.meetingId);

    if (event.eventType === 'vc.bot.meeting_ended_v1') {
      await this._recoverMeeting(before);
      await this.flushMeeting(event.meetingId, { all: true });
      this.store.finishMeeting(event.meetingId, event.createTimeMs);
    } else if (event.recoveryRequested || gap) {
      await this._recoverMeeting(this.store.meetingSessionById(event.meetingId));
    }
    return inserted;
  }

  async _handleActivityItems(event) {
    let insertedAny = false;
    const touched = new Set();
    for (const value of event.activityItems) {
      const item = object(value);
      const meeting = object(namedValue(item, new Set(['meeting'])));
      const meetingId = textValue(
        namedValue(item, new Set(['meeting_id', 'meetingId'])) ?? meeting.id,
      );
      if (!meetingId) throw new Error('meeting activity item is missing meeting.id');
      const meetingNumber = textValue(namedValue(meeting, new Set([
        'meeting_no',
        'meeting_number',
        'meetingNo',
        'meetingNumber',
      ])));
      const activityType = firstText(item, [
        'activity_event_type',
        'activity_type',
        'activityEventType',
        'event_type',
      ]) ?? event.activityType;
      const sequenceNumber = Number.parseInt(String(
        namedValue(item, new Set(['event_seq', 'activity_seq', 'sequence'])) ?? '',
      ), 10);
      const sequence = Number.isSafeInteger(sequenceNumber) && sequenceNumber >= 0
        ? sequenceNumber
        : event.sequence;
      const itemAtMs = timestampMs(namedValue(item, new Set([
        'event_time',
        'create_time',
        'timestamp',
        'start_time_ms',
        'send_time',
        'time',
      ])), event.createTimeMs);

      this.store.ensureMeetingSession(meetingId, { meetingNumber, atMs: itemAtMs });
      const gap = this.store.meetingHasSequenceGap(meetingId, sequence);
      let latest = itemAtMs;
      let inserted = false;
      for (const fragment of semanticFragments(item, meetingId, activityType, itemAtMs)) {
        latest = Math.max(latest, fragment.occurredAtMs);
        inserted = this.store.ingestMeetingEvent({
          eventKey: fragment.eventKey,
          meetingId,
          eventType: event.eventType,
          activityType,
          occurredAtMs: fragment.occurredAtMs,
          payload: fragment.payload,
        }) || inserted;
      }
      this.store.updateMeetingProgress(meetingId, {
        pageToken: event.pageToken,
        sequence,
        eventAtMs: latest,
      });
      if (inserted) {
        insertedAny = true;
        this._schedule(meetingId, meetingActivityDebounceMs(item, activityType));
      }
      touched.add(meetingId);
      if (event.recoveryRequested || gap) {
        await this._recoverMeeting(this.store.meetingSessionById(meetingId));
      }
    }
    return insertedAny || touched.size > 0;
  }

  async _handleInvite(event) {
    if (!event.meetingNumber) throw new Error('meeting invite is missing meeting number');
    const key = event.meetingId
      ? `meeting:${event.meetingId}`
      : event.callId
        ? `call:${event.callId}`
        : `invite:${event.eventId}`;
    const row = this.store.upsertMeetingInvite({
      sessionKey: key,
      meetingId: event.meetingId,
      meetingNumber: event.meetingNumber,
      callId: event.callId,
      eventId: event.eventId,
      atMs: event.createTimeMs,
    });
    if (['JOINED', 'ACTIVE', 'ENDED', 'JOINING'].includes(row.status)) return false;
    this.store.markMeetingJoining(key, event.createTimeMs);
    try {
      const result = await this.lark.joinMeeting({
        meetingNumber: event.meetingNumber,
        callId: event.callId,
      });
      const joined = joinMeetingFrom(result);
      if (!joined.meetingId) throw new Error('meeting join returned no meeting.id');
      this.store.markMeetingJoined(key, {
        meetingId: joined.meetingId,
        meetingNumber: joined.meetingNumber ?? event.meetingNumber,
        atMs: Date.now(),
      });
      this.logger.info('meeting_joined', { meetingRef: opaqueRef(joined.meetingId) });
      return true;
    } catch (error) {
      this.store.markMeetingJoinFailed(key, safe(error));
      throw error;
    }
  }

  _schedule(meetingId, delayMs = this.batchDelayMs) {
    if (this.stopped) return;
    const now = this.now();
    const window = this.batchWindows.get(meetingId) ?? { startedAtMs: now };
    const stable = delayMs <= this.batchDelayMs;
    if (window.stable && !stable && this.timers.has(meetingId)) return;
    Object.assign(window, { lastAtMs: now, stable });
    this.batchWindows.set(meetingId, window);
    if (this.timers.has(meetingId)) this.clearTimer(this.timers.get(meetingId));
    const due = Math.min(now + delayMs, window.startedAtMs + this.batchMaxDelayMs);
    const timer = this.setTimer(() => {
      this.timers.delete(meetingId);
      this.batchWindows.delete(meetingId);
      void this.flushMeeting(meetingId).catch((error) => {
        this.logger.warn('meeting_batch_flush_failed', {
          meetingRef: opaqueRef(meetingId),
          error: safe(error),
        });
      });
    }, Math.max(0, due - now));
    timer?.unref?.();
    this.timers.set(meetingId, timer);
  }

  async flushMeeting(meetingId, { all = false } = {}) {
    if (this.timers.has(meetingId)) this.clearTimer(this.timers.get(meetingId));
    this.timers.delete(meetingId);
    this.batchWindows.delete(meetingId);
    let batches = 0;
    do {
      const rows = this.store.pendingMeetingEvents(meetingId, this.batchMaxEvents);
      if (!rows.length) break;
      const selected = [];
      let chars = 0;
      for (const row of rows) {
        const size = meetingContextLines(row.payload, row.activityType).join(' ⏎ ').length;
        if (selected.length && chars + size > this.batchMaxChars) break;
        selected.push(row);
        chars += size;
      }
      const batch = this.store.commitMeetingInboxBatch(
        meetingId,
        selected,
        selected.at(-1).occurredAtMs,
      );
      if (batch) {
        try {
          await this.onInboxReady?.(batch);
        } catch (error) {
          this.logger.warn('meeting_inbox_notify_failed', {
            meetingRef: opaqueRef(meetingId),
            error: safe(error),
          });
        }
      }
      batches += 1;
      if (!all) break;
    } while (true);

    if (!all) {
      const next = this.store.pendingMeetingEvents(meetingId, 1)[0];
      if (next) {
        this._schedule(
          meetingId,
          meetingActivityDebounceMs(next.payload, next.activityType),
        );
      }
    }
    return batches;
  }

  async _recoverMeeting(row) {
    if (!row?.meetingId || row.status === 'ENDED') return false;
    try {
      const response = await this.lark.listMeetingEvents({
        meetingId: row.meetingId,
        pageToken: row.pageToken,
        startMs: row.pageToken ? null : row.lastEventAtMs,
      });
      const envelope = pullEnvelope(response);
      let inserted = false;
      for (const item of envelope.events) {
        const type = firstText(item, [
          'event_type',
          'activity_event_type',
          'activity_type',
        ]) ?? 'recovered_activity';
        const at = timestampMs(namedValue(item, new Set([
          'occurred_at_ms',
          'event_time',
          'create_time',
          'timestamp',
          'start_time',
        ])), Date.now());
        for (const fragment of semanticFragments(item, row.meetingId, type, at)) {
          inserted = this.store.ingestMeetingEvent({
            eventKey: fragment.eventKey,
            meetingId: row.meetingId,
            eventType: 'vc.bot.meeting_activity_v1',
            activityType: type,
            occurredAtMs: fragment.occurredAtMs,
            payload: fragment.payload,
          }) || inserted;
        }
      }
      this.store.updateMeetingProgress(row.meetingId, {
        pageToken: envelope.pageToken,
        eventAtMs: Date.now(),
      });
      if (inserted) {
        const next = this.store.pendingMeetingEvents(row.meetingId, 1)[0];
        this._schedule(
          row.meetingId,
          meetingActivityDebounceMs(next?.payload, next?.activityType),
        );
      }
      return true;
    } catch (error) {
      if (/20001|MEETING_END/i.test(String(error))) {
        this.store.finishMeeting(row.meetingId);
      }
      this.logger.warn('meeting_recovery_failed', {
        meetingRef: opaqueRef(row.meetingId),
        error: safe(error),
      });
      return false;
    }
  }

  async start() {
    for (const id of this.store.pendingMeetingIds()) {
      const next = this.store.pendingMeetingEvents(id, 1)[0];
      this._schedule(id, meetingActivityDebounceMs(next?.payload, next?.activityType));
    }
    for (const row of this.store.activeMeetingSessions()) {
      await this._recoverMeeting(row);
    }
  }

  async stop() {
    this.stopped = true;
    for (const timer of this.timers.values()) this.clearTimer(timer);
    this.timers.clear();
    this.batchWindows.clear();
    await this.ingress.catch(() => {});
    for (const id of this.store.pendingMeetingIds()) {
      await this.flushMeeting(id, { all: true });
    }
  }
}
