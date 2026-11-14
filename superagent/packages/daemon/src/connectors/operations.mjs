// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Connector operation name -> client call. Outbound actions and queries arrive
// here only after the Core has checked authority, freshness, content and
// idempotency; a client exposes only the operations its manifest declares.
const OPERATIONS = {
  'message.send': (client, input) => client.sendMessage(input),
  'message.reply': (client, input) => client.replyMessage(input),
  'message.react': (client, input) => client.reactMessage(input),
  'message.reaction.delete': (client, input) => client.deleteReaction(input),
  'message.retract': (client, input) => client.retractMessage(input),
  'message.media': (client, input) => client.getMedia(input),
  'chat.list': (client, input) => client.listChats(input),
  'chat.messages': (client, input) => client.listChatMessages(input),
  'message.search': (client, input) => client.searchMessages(input),
  'thread.messages': (client, input) => client.listThreadMessages(input),
  'chat.members.list': (client, input) => client.listChatMembers(input.chat_id, input),
  'chat.get': (client, input) => client.getChatName(input.chat_id),
  'card.update': (client, input) => client.updateCard(input.message_id, input.card),
  'meeting.join': (client, input) => client.joinMeeting(input),
  'meeting.leave': (client, input) => client.leaveMeeting(input.meeting_id),
  'meeting.events.list': (client, input) => client.listMeetingEvents(input),
};

export async function invokeOperation(client, name, input) {
  const operation = OPERATIONS[name];
  if (!operation) throw new Error(`unsupported connector action: ${name}`);
  return await operation(client, input);
}
