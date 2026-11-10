// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { normalizeLarkMessage as normalizeEvent } from '../src/connectors/lark-events.mjs';
import { deterministicFilter as filterEvent } from '../src/event.mjs';
import { chatEvent } from './event-fixture.mjs';
const deterministicFilter = (event, config) => filterEvent(event.envelope ? event : chatEvent(event), config);

function config(overrides = {}) {
  return {
    ignoredSenderIds: new Set(),
    maxEventAgeMs: 86_400_000,
    ...overrides,
  };
}

describe('normalizeEvent', () => {
  it('preserves raw thread scope and human-readable text', () => {
    const now = Date.now();
    const event = normalizeEvent({
      schema: '2.0',
      header: {
        event_id: 'evt-1',
        event_type: 'im.message.receive_v1',
        tenant_key: 'tenant-1',
        create_time: String(now),
      },
      event: {
        sender: {
          sender_id: { open_id: 'ou-user' },
          sender_type: 'user',
        },
        message: {
          message_id: 'om-reply',
          root_id: 'om-root',
          parent_id: 'om-root',
          thread_id: 'omt-topic',
          chat_id: 'oc-chat',
          chat_type: 'group',
          message_type: 'text',
          content: JSON.stringify({ text: '帮我看一下这个问题' }),
          create_time: String(now),
        },
      },
    }, 'work');

    assert.equal(event.content, '帮我看一下这个问题');
    assert.equal(event.senderType, 'user');
    assert.equal(event.threadId, 'omt-topic');
    assert.equal(event.threadRootId, 'om-root');
    assert.equal(event.conversationKey, JSON.stringify(['work', 'conversation', 'tenant-1', 'oc-chat', 'omt-topic']));
  });

  it('keeps another bot\'s message marked as a bot even without a user id', async () => {
    const { formatInboundLine } = await import('../src/runtime-input.mjs');
    const event = normalizeEvent({
      header: { event_id: 'evt-bot', event_type: 'im.message.receive_v1', tenant_key: 'tenant-1' },
      event: {
        sender: { sender_id: {}, sender_type: 'app' },
        message: { message_id: 'om-bot', chat_id: 'oc-chat', chat_type: 'group', message_type: 'text',
          content: JSON.stringify({ text: 'build failed' }), create_time: String(Date.now()) },
      },
    });
    assert.deepEqual(event.actor, { id: 'bot', type: 'app' });
    assert.match(formatInboundLine(event), / sender=bot\] build failed$/);
  });

  it('keeps an ordinary quoted reply in the main conversation without thread_id', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-quoted',
      root_id: 'om-root',
      parent_id: 'om-root',
      chat_id: 'oc-chat',
      chat_type: 'group',
      message_type: 'text',
      content: '普通引用回复',
      sender_id: 'ou-user',
      timestamp: String(Date.now()),
    });
    assert.equal(event.threadId, null);
    assert.equal(event.threadRootId, null);
    assert.equal(event.conversationKey, JSON.stringify(['lark', 'conversation', '-', 'oc-chat', null]));
  });

  it('accepts the compact compatibility format', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      event_id: 'evt-2',
      message_id: 'om-2',
      chat_id: 'oc-2',
      chat_type: 'p2p',
      message_type: 'text',
      content: 'hello',
      sender_id: 'ou-2',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, 'hello');
    assert.equal(event.conversationKey, JSON.stringify(['lark', 'conversation', '-', 'oc-2', null]));
  });

  it('does not leak rich-text tag names into post content', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-post',
      chat_id: 'oc-post',
      chat_type: 'group',
      message_type: 'post',
      content: JSON.stringify({
        zh_cn: {
          title: '结论',
          content: [[{ tag: 'text', text: '已经完成' }]],
        },
      }),
      sender_id: 'ou-post',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '结论\n已经完成');
  });

  it('surfaces the image_key so view_lark_image can fetch the picture', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-image',
      chat_id: 'oc-image',
      chat_type: 'p2p',
      message_type: 'image',
      content: JSON.stringify({ image_key: 'img_v3_abc123' }),
      sender_id: 'ou-image',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '[image image_key=img_v3_abc123]');
  });

  it('preserves image references embedded in a rich post in content order', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-post-image',
      chat_id: 'oc-post-image',
      chat_type: 'group',
      message_type: 'post',
      content: JSON.stringify({
        content: [[
          { tag: 'text', text: '先看' },
          { tag: 'img', image_key: 'img_v3_first' },
          { tag: 'text', text: '再看' },
          { tag: 'img', image_key: 'img_v3_second' },
          { tag: 'img', image_key: 'img_v3_first' },
        ]],
      }),
      sender_id: 'ou-post',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '先看\n[image image_key=img_v3_first]\n再看\n[image image_key=img_v3_second]');
  });

  it('does not turn malformed post image keys into resource references', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-post-bad-image',
      chat_id: 'oc-post-bad-image',
      chat_type: 'group',
      message_type: 'post',
      content: JSON.stringify({ content: [[
        { tag: 'img', image_key: 'not-an-image-key' },
        { tag: 'text', text: '请看附件' },
      ]] }),
      sender_id: 'ou-post',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '请看附件');
  });

  it('surfaces the file_key for file/audio/video/sticker messages', () => {
    const fileEvent = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-file',
      chat_id: 'oc-file',
      chat_type: 'p2p',
      message_type: 'file',
      content: JSON.stringify({ file_key: 'file_v3_abc123', file_name: 'report.pdf' }),
      sender_id: 'ou-file',
      timestamp: String(Date.now()),
    });
    assert.equal(fileEvent.content, '[file file_key=file_v3_abc123 file_name=report.pdf]');

    const audioEvent = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-audio',
      chat_id: 'oc-audio',
      chat_type: 'p2p',
      message_type: 'audio',
      content: JSON.stringify({ file_key: 'file_v3_def456', duration: 1200 }),
      sender_id: 'ou-audio',
      timestamp: String(Date.now()),
    });
    assert.equal(audioEvent.content, '[audio file_key=file_v3_def456]');
  });

  it('turns a bodyless video-chat notice into an Agent-visible control signal', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-video-chat',
      chat_id: 'oc-video-chat',
      chat_type: 'p2p',
      message_type: 'video_chat',
      content: '{}',
      sender_id: 'ou-user',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '[Video call]');
  });

  it('delivers readable forwarded conversations instead of filtering them as unknown', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-forward',
      chat_id: 'oc-forward',
      chat_type: 'group',
      message_type: 'merge_forward',
      content: '<forwarded_messages>\nAda: 请 review #42\n</forwarded_messages>',
      sender_id: 'ou-user',
      timestamp: String(Date.now()),
    });
    assert.match(event.content, /Ada: 请 review #42/);
    assert.equal(deterministicFilter(event, config()), null);
  });

  it('keeps a card visible even when its receive event carries no extractable body', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-card-empty',
      chat_id: 'oc-card',
      chat_type: 'group',
      message_type: 'interactive',
      content: '{}',
      sender_id: 'ou-user',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '[card]');
  });

  it('prefers the card body when the receive event does carry one', () => {
    const event = normalizeEvent({
      type: 'im.message.receive_v1',
      message_id: 'om-card-text',
      chat_id: 'oc-card',
      chat_type: 'group',
      message_type: 'interactive',
      content: JSON.stringify({ elements: [{ tag: 'div', text: { content: '打个 reaction 就行' } }] }),
      sender_id: 'ou-user',
      timestamp: String(Date.now()),
    });
    assert.equal(event.content, '打个 reaction 就行');
  });
});

describe('deterministicFilter', () => {
  it('allows supported media and filters unsupported types and explicit deny-list senders', () => {
    const base = {
      type: 'im.message.receive_v1',
      senderType: 'user',
      senderId: 'ou-user',
      chatId: 'oc-chat',
      chatType: 'p2p',
      messageType: 'text',
      content: 'hello',
      createTimeMs: Date.now(),
    };
    assert.equal(deterministicFilter({ ...base, senderType: 'bot' }, config()), null);
    assert.equal(deterministicFilter({ ...base, senderType: 'app' }, config()), null);
    assert.equal(deterministicFilter({ ...base, messageType: 'image', content: '[image image_key=img_v3_abc123]' }, config()), null);
    assert.equal(
      deterministicFilter(base, config({ ignoredSenderIds: new Set(['ou-user']) })),
      'ignored_sender',
    );
  });

  it('considers both visible groups and P2P conversations', () => {
    const base = {
      type: 'im.message.receive_v1',
      senderType: 'user',
      senderId: 'ou-user',
      chatId: 'oc-chat',
      chatType: 'group',
      messageType: 'text',
      content: 'hello',
      createTimeMs: Date.now(),
    };
    assert.equal(deterministicFilter(base, config()), null);
    assert.equal(deterministicFilter({ ...base, chatType: 'p2p' }, config()), null);
  });

  it('delivers video-chat control signals outside the ordinary media allowlist', () => {
    const event = {
      type: 'im.message.receive_v1',
      senderType: 'user',
      senderId: 'ou-user',
      chatId: 'oc-chat',
      chatType: 'p2p',
      messageType: 'video_chat',
      content: '[Video call]',
      createTimeMs: Date.now(),
    };
    assert.equal(deterministicFilter(event, config()), null);
  });

  it('delivers cards outside the ordinary media allowlist', () => {
    const event = {
      type: 'im.message.receive_v1',
      senderType: 'user',
      senderId: 'ou-user',
      chatId: 'oc-chat',
      chatType: 'group',
      messageType: 'interactive',
      content: '[card]',
      createTimeMs: Date.now(),
    };
    assert.equal(deterministicFilter(event, config({
    })), null);
  });
});
