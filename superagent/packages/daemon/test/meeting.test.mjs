// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { cognitionHistoryPrefix } from '../src/cognition.mjs';
import {
  MeetingAdapter,
  MeetingInboxNotifier,
  meetingActivityDebounceMs,
  meetingContextLines,
} from '../src/meeting/index.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent } from './event-fixture.mjs';
import { normalizeLarkEvent } from '../src/connectors/lark-events.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function envelope(type, id, event, atMs = Date.now()) {
  return normalizeLarkEvent({
    schema: '2.0',
    header: {
      event_id: id,
      event_type: type,
      tenant_key: 'tenant-test',
      create_time: String(atMs),
    },
    event,
  }).data.meeting;
}

describe('TAT meeting adapter', () => {
  let directory;
  let store;

  beforeEach(() => {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-meeting-test-'));
    store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
  });

  afterEach(() => {
    try { store.close(); } catch {}
    fs.rmSync(directory, { recursive: true, force: true });
  });

  it('normalizes invite correlation fields without applying the IM schema', () => {
    const parsed = envelope('vc.bot.meeting_invited_v1', 'evt-invite', {
      meeting: { id: '734000000000000001', meeting_no: '123456789' },
      call_id: 'call-123',
    });
    assert.equal(parsed.meetingNumber, '123456789');
    assert.equal(parsed.callId, 'call-123');
    assert.equal(parsed.meetingId, '734000000000000001');
  });

  it('auto-joins once and batches transcripts into the durable inbox, never the journal', async () => {
    const joins = [];
    const lark = {
      async joinMeeting(input) {
        joins.push(input);
        return { ok: true, data: { meeting: { id: '734000000000000001', meeting_no: '123456789' } } };
      },
      async listMeetingEvents() { throw new Error('pull is recovery-only'); },
    };
    const inboxBatches = [];
    const adapter = new MeetingAdapter({
      store: store.meetings,
      lark,
      logger: quietLogger,
      batchDelayMs: 60_000,
      onInboxReady: (batch) => inboxBatches.push(batch),
    });
    const invite = envelope('vc.bot.meeting_invited_v1', 'evt-invite', {
      meeting: { id: '734000000000000001', meeting_no: '123456789' },
      call_id: 'call-123',
    });
    await adapter.handleEvent(invite);
    await adapter.handleEvent(invite); // duplicate delivery must not create a second visible join
    assert.deepEqual(joins, [{ meetingNumber: '123456789', callId: 'call-123' }]);

    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a1', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          text: '第一句（partial）',
          sentence_id: 'sentence-1',
        }],
      }],
    }));
    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a1-final', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          text: '第一句',
          sentence_id: 'sentence-1',
        }],
      }],
    }));
    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a2', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-bob', user_name: 'Bob' },
          text: '第二句',
          sentence_id: 'sentence-2',
        }],
      }],
    }));
    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a2', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-bob', user_name: 'Bob' },
          text: '第二句',
          sentence_id: 'sentence-2',
        }],
      }],
    }));

    assert.equal(store.stats().meetingEvents, 2);
    assert.equal(store.cognitionJournal().filter((row) => row.kind === 'meeting_context').length, 0);
    await adapter.flushMeeting('734000000000000001');
    assert.equal(store.cognitionJournal().filter((row) => row.kind === 'meeting_context').length, 0);
    assert.equal(inboxBatches.length, 1);
    assert.equal(inboxBatches[0].pendingCount, 2);
    const read = store.readMeetingInbox();
    assert.equal(read.items.length, 2);
    assert.equal(read.remaining, 0);
    const rendered = read.items.flatMap((row) => meetingContextLines(row.payload, row.activityType)).join('\n');
    assert.match(rendered, /Alice: 第一句/);
    assert.doesNotMatch(rendered, /partial/);
    assert.match(rendered, /Bob: 第二句/);

    // A correction that arrives after the earlier item was read is a new
    // generation instead of being frozen or silently dropped.
    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a1-corrected', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          text: '第一句（最终修正）',
          sentence_id: 'sentence-1',
        }],
      }],
    }));
    await adapter.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-a1-corrected-again', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000001', meeting_no: '123456789' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          text: '第一句（最终修正 v2）',
          sentence_id: 'sentence-1',
        }],
      }],
    }));
    await adapter.flushMeeting('734000000000000001');
    const correction = store.readMeetingInbox();
    assert.equal(correction.items.length, 1);
    assert.match(JSON.stringify(correction.items[0].payload), /最终修正 v2/);
    assert.doesNotMatch(JSON.stringify(correction.items[0].payload), /第一句（最终修正）/);
    assert.equal(store.stats().meetingEvents, 3, 'post-ack revisions coalesce into one pending generation');
    assert.equal(inboxBatches.length, 2);

    // The next real IM turn must not receive transcript payload via cognition catch-up.
    const inbound = store.ingest(chatEvent({
      eventId: 'evt-im',
      messageId: 'om-im',
      conversationKey: 'test:tenant:oc-chat:main',
      chatId: 'oc-chat',
      chatType: 'p2p',
      messageType: 'text',
      senderId: 'ou-user',
      senderType: 'user',
      rootId: null,
      parentId: null,
      threadId: null,
      threadRootId: null,
      content: '会议讲到哪了？',
      mentions: [],
      createTimeMs: Date.now(),
    })).inbound;
    const through = store.cognitionJournalBeforeInbound(inbound.seq);
    const prefix = cognitionHistoryPrefix(store, store.getCognitionState().epoch, through).text;
    assert.doesNotMatch(prefix, /kind=meeting_context/);
    assert.doesNotMatch(prefix, /第一句|第二句/);
    await adapter.stop();
  });

  it('uses a saved cursor only during restart recovery and persists recovered transcript', async () => {
    store.upsertMeetingInvite({
      sessionKey: 'call:recover',
      meetingNumber: '987654321',
      callId: 'recover',
      eventId: 'evt-recover-invite',
    });
    store.markMeetingJoining('call:recover');
    store.markMeetingJoined('call:recover', {
      meetingId: '734000000000000099',
      meetingNumber: '987654321',
    });
    store.updateMeetingProgress('734000000000000099', { pageToken: 'cursor-before' });
    const pulls = [];
    const adapter = new MeetingAdapter({
      store: store.meetings,
      logger: quietLogger,
      batchDelayMs: 60_000,
      lark: {
        async joinMeeting() { throw new Error('not expected'); },
        async listMeetingEvents(input) {
          pulls.push(input);
          return {
            ok: true,
            data: {
              events: [{
                event_type: 'transcript_received',
                event_time: '2026-08-10T12:00:00Z',
                payload: {
                  transcript_received_items: [{
                    speaker: { id: 'ou-carol', user_name: 'Carol' },
                    text: '恢复内容',
                    sentence_id: 'sentence-recovered',
                  }],
                },
              }],
              page_token: 'cursor-after',
              has_more: false,
            },
          };
        },
      },
    });
    await adapter.start();
    assert.equal(pulls.length, 1);
    assert.equal(pulls[0].pageToken, 'cursor-before');
    await adapter.stop();
    assert.equal(store.meetingSessionById('734000000000000099').pageToken, 'cursor-after');
    assert.equal(store.cognitionJournal().filter((row) => row.kind === 'meeting_context').length, 0);
    const read = store.readMeetingInbox();
    assert.equal(read.items.length, 1);
    assert.equal(read.items[0].occurredAtMs, Date.parse('2026-08-10T12:00:00Z'));
    assert.match(JSON.stringify(read.items[0].payload), /恢复内容/);
  });

  it('dedupes push and restart pull copies with equivalent transcript semantics', async () => {
    const push = new MeetingAdapter({
      store: store.meetings,
      logger: quietLogger,
      batchDelayMs: 60_000,
      lark: {
        async joinMeeting() { throw new Error('not expected'); },
        async listMeetingEvents() { throw new Error('not expected before restart'); },
      },
    });
    await push.handleEvent(envelope('vc.bot.meeting_activity_v1', 'evt-live-copy', {
      meeting_activity_items: [{
        meeting: { id: '734000000000000555', meeting_no: '555555555' },
        activity_event_type: 'transcript_received',
        actors: [{ open_id: 'ou-alice', display_name: 'Alice' }],
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          sentence_id: 'sentence-same',
          text: '同一份逐字稿',
          start_time_ms: '1786371738000',
          end_time_ms: '1786371738442',
        }],
      }],
    }));
    await push.flushMeeting('734000000000000555');
    assert.equal(store.meetings.readMeetingInbox().items.length, 1);
    await push.stop();

    const notifications = [];
    const recovered = new MeetingAdapter({
      store: store.meetings,
      logger: quietLogger,
      batchDelayMs: 60_000,
      onInboxReady: (batch) => notifications.push(batch),
      lark: {
        async joinMeeting() { throw new Error('not expected'); },
        async listMeetingEvents() {
          return {
            ok: true,
            data: {
              events: [{
                event_type: 'transcript_received',
                event_time: '2026-08-10T12:00:00Z',
                payload: {
                  transcript_received_items: [{
                    speaker: 'Alice',
                    sentence_id: 'sentence-same',
                    text: '同一份逐字稿',
                    start_time_ms: '2026-08-10T12:00:00.000Z',
                    end_time_ms: '2026-08-10T12:00:00.442Z',
                  }],
                },
              }],
              has_more: false,
            },
          };
        },
      },
    });
    await recovered.start();
    assert.equal(store.meetings.unreadMeetingInboxCount(), 0);
    assert.equal(store.meetings.pendingMeetingEvents('734000000000000555').length, 0);
    assert.equal(store.stats().meetingEvents, 1);
    assert.deepEqual(notifications, [], 'restart recovery must not replay an acknowledged semantic duplicate');
    await recovered.stop();
  });

  it('merges an activity-created placeholder when join completion arrives', () => {
    store.upsertMeetingInvite({
      sessionKey: 'call:race',
      meetingNumber: '123456789',
      callId: 'race',
      eventId: 'evt-race',
    });
    store.markMeetingJoining('call:race');
    store.ensureMeetingSession('734000000000000777', {
      meetingNumber: '123456789',
      atMs: 100,
    });
    store.updateMeetingProgress('734000000000000777', {
      pageToken: 'cursor-from-push',
      eventAtMs: 200,
    });
    const joined = store.markMeetingJoined('call:race', {
      meetingId: '734000000000000777',
      meetingNumber: '123456789',
      atMs: 300,
    });
    assert.equal(joined.sessionKey, 'call:race');
    assert.equal(joined.pageToken, 'cursor-from-push');
    assert.equal(store.activeMeetingSessions().length, 1);
  });

  it('coalesces simultaneous inbox generations and wakes or steers the runtime', () => {
    const deliveries = [];
    const notified = [];
    const timers = [];
    const runtime = {
      turnInProgress: true,
      submit(items) {
        const [{ text, seq }] = items;
        deliveries.push({ text, seq, active: this.turnInProgress });
        return { accepted: true, disposition: 'enqueued' };
      },
    };
    const notifier = new MeetingInboxNotifier({
      store: {
        unreadMeetingInboxCount: () => 7,
        markMeetingInboxBatchesNotified: (ids) => notified.push(ids),
      },
      runtime,
      logger: quietLogger,
      setTimer(callback) { timers.push(callback); return { unref() {} }; },
      clearTimer() {},
    });
    notifier.signal({ batchId: 10 });
    notifier.signal({ batchId: 10 });
    notifier.signal({ batchId: 11 });
    assert.equal(timers.length, 1);
    timers.shift()();
    assert.deepEqual(deliveries, [{
      text: '7 pending meeting messages; use read_meeting_inbox to read.',
      seq: null,
      active: true,
    }]);
    assert.deepEqual(notified, [[10, 11]]);

    notifier.signal({ batchId: 12 });
    assert.equal(timers.length, 1);
    timers.shift()();
    assert.equal(deliveries.length, 2);
    assert.deepEqual(notified[1], [12]);
  });

  it('uses 1s for closed items, 3s for partials, and caps partial churn at 4s', async () => {
    assert.equal(meetingActivityDebounceMs({
      transcript_received_items: [{ text: 'closed', end_time_ms: '1786371738442' }],
    }, 'transcript_received'), 1_000);
    assert.equal(meetingActivityDebounceMs({
      transcript_received_items: [{ text: 'partial' }],
    }, 'transcript_received'), 3_000);
    assert.equal(meetingActivityDebounceMs({ chat_received_items: [{ text: 'hi' }] }, 'chat_received'), 1_000);
    let now = 1_000;
    const scheduled = [];
    const cleared = [];
    const adapter = new MeetingAdapter({
      store: store.meetings,
      logger: quietLogger,
      lark: {
        async joinMeeting() { throw new Error('not expected'); },
        async listMeetingEvents() { throw new Error('not expected'); },
      },
      batchDelayMs: 1_000,
      batchMaxDelayMs: 4_000,
      now: () => now,
      setTimer(callback, delay) {
        const timer = { callback, delay, unref() {} };
        scheduled.push(timer);
        return timer;
      },
      clearTimer: (timer) => cleared.push(timer),
    });
    const activity = (eventId, text, endTimeMs = null) => envelope('vc.bot.meeting_activity_v1', eventId, {
      meeting_activity_items: [{
        meeting: { id: '734000000000000222', meeting_no: '222222222' },
        activity_event_type: 'transcript_received',
        transcript_received_items: [{
          speaker: { id: 'ou-alice', user_name: 'Alice' },
          text,
          sentence_id: 'sentence-live',
          ...(endTimeMs === null ? {} : { end_time_ms: endTimeMs }),
        }],
      }],
    });

    await adapter.handleEvent(activity('evt-partial-1', '请帮我'));
    assert.equal(scheduled.at(-1).delay, 3_000);
    now = 1_600;
    await adapter.handleEvent(activity('evt-partial-2', '请帮我记录'));
    assert.equal(cleared.length, 1);
    assert.equal(scheduled.at(-1).delay, 3_000);
    now = 4_500;
    await adapter.handleEvent(activity('evt-partial-3', '请帮我记录结论'));
    assert.equal(scheduled.at(-1).delay, 500, 'the original 4s max-age wins over another 1s reset');

    await adapter.flushMeeting('734000000000000222');
    const read = store.readMeetingInbox();
    assert.equal(read.items.length, 1);
    assert.match(JSON.stringify(read.items[0].payload), /请帮我记录结论/);

    now = 10_000;
    await adapter.handleEvent(activity('evt-partial-later', '新的草稿'));
    assert.equal(scheduled.at(-1).delay, 3_000);
    now = 10_500;
    await adapter.handleEvent(activity('evt-stable-later', '新的完整句子', '1786371738442'));
    assert.equal(scheduled.at(-1).delay, 1_000, 'a closed revision pulls the partial window forward');
    await adapter.stop();
  });
});
