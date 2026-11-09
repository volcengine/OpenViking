// SDK callbacks flatten the authenticated provider header and event.
export function sdkEventEnvelope(eventType, data, allowedChats = new Set()) {
  const chatId = data.message?.chat_id ?? data.context?.open_chat_id ?? data.chat_id;
  if (allowedChats.size && !allowedChats.has(chatId)) return null;
  const { token: _token, encrypt: _encrypt, ...event } = data;
  return { schema: data.schema ?? '2.0', header: {
    event_id: data.event_id, event_type: eventType, create_time: data.create_time,
    tenant_key: data.tenant_key, app_id: data.app_id,
  }, event };
}
