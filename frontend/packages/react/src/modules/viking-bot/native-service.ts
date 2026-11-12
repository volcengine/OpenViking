import { OV_PATHS, OV_PATH_BUILDERS } from '../../client/paths.js';
import type {
  OvClient,
  OvRequestOptions,
  OvRequestSpec,
} from '../../client/types.js';
import type { VikingBotSession } from '../../components/viking-bot/capabilities.js';
import { nativeChatEvents } from './native-events.js';
import { readNativeMessages, writeNativeParts } from './native-messages.js';
import { nativeChatInput } from './native-input.js';
import type { VikingBotChatService, VikingBotSessionService } from './types.js';

interface SessionDTO {
  session_id: string;
  mod_time?: string;
  updated_at?: string;
}
const sessionView = (item: SessionDTO): VikingBotSession => ({
  id: item.session_id,
  title: item.session_id,
  updatedAt: item.updated_at ?? item.mod_time,
});

export function createNativeBotService(
  client: OvClient,
  options: OvRequestOptions,
): VikingBotChatService & VikingBotSessionService {
  const request = <T>(spec: OvRequestSpec, signal: AbortSignal) =>
    client.request<T>({ ...spec, options: { ...options, signal } });
  return {
    async *stream(input, signal) {
      const source = await client.stream({
        method: 'POST',
        path: OV_PATHS.botChatStream,
        body: nativeChatInput(input),
        options: { ...options, signal },
      });
      yield* nativeChatEvents(source);
    },
    async createSession(signal) {
      return sessionView(
        await request<SessionDTO>(
          { method: 'POST', path: OV_PATHS.sessions, body: {} },
          signal,
        ),
      );
    },
    async listSessions(signal) {
      const items = await request<SessionDTO[]>(
        { method: 'GET', path: OV_PATHS.sessions },
        signal,
      );
      return items
        .map(sessionView)
        .sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
    },
    async removeSession(id, signal) {
      await request(
        { method: 'DELETE', path: OV_PATH_BUILDERS.session(id) },
        signal,
      );
    },
    async loadMessages(id, signal) {
      const path = OV_PATH_BUILDERS.session(id);
      const [context, meta] = await Promise.all([
        request<{ messages: Parameters<typeof readNativeMessages>[0] }>(
          { method: 'GET', path: OV_PATH_BUILDERS.sessionContext(id) },
          signal,
        ),
        request<{ commit_count: number }>({ method: 'GET', path }, signal),
      ]);
      const archives: Parameters<typeof readNativeMessages>[0][] = [];
      // Four concurrent archive reads per instance; preserve chronological order.
      for (let offset = 0; offset < meta.commit_count; offset += 4) {
        const batch = await Promise.all(
          Array.from(
            { length: Math.min(4, meta.commit_count - offset) },
            async (_, index) => {
              const archive = `archive_${String(offset + index + 1).padStart(3, '0')}`;
              const result = await request<{
                messages: Parameters<typeof readNativeMessages>[0];
              }>(
                {
                  method: 'GET',
                  path: OV_PATH_BUILDERS.sessionArchive(id, archive),
                },
                signal,
              );
              return result.messages;
            },
          ),
        );
        archives.push(...batch);
      }
      const seen = new Set<string>();
      return readNativeMessages(
        [...archives.flat(), ...context.messages].filter((message) => {
          if (seen.has(message.id)) return false;
          seen.add(message.id);
          return true;
        }),
      );
    },
    async saveMessage(id, message, signal) {
      const parts = writeNativeParts(message.parts);
      if (!parts.length) return;
      await request(
        {
          method: 'POST',
          path: OV_PATH_BUILDERS.sessionMessages(id),
          body: {
            role: message.role,
            parts,
            turn_id: message.turnId,
            created_at: message.createdAt,
            message_kind:
              message.role === 'user' ? 'user_query' : 'assistant_step',
          },
        },
        signal,
      );
    },
    interrupt: (id, signal) =>
      request(
        {
          method: 'POST',
          path: OV_PATH_BUILDERS.botInterrupt(id),
        },
        signal,
      ),
    steer: (input, signal) =>
      request(
        {
          method: 'POST',
          path: OV_PATH_BUILDERS.botSteer(input.sessionId),
          body: nativeChatInput(input),
        },
        signal,
      ),
  };
}
