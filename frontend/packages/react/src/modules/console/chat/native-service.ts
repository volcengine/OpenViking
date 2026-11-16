import { nativeService } from '../../../shared/monitoring/service.js';
import { OV_PATHS, OV_PATH_BUILDERS } from '../../../client/paths.js';
import type {
  OvClient,
  OvRequestOptions,
  OvRequestSpec,
} from '../../../client/types.js';
import type { VikingBotSession } from '../../../components/viking-bot/capabilities.js';
import { isOvClientError } from '../../../client/errors.js';
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
  return nativeService(
    {
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
        const meta = await request<{ commit_count: number; uri: string }>(
          { method: 'GET', path },
          signal,
        );
        const readMessages = async (uri: string) => {
          const content = await request<string>(
            {
              method: 'GET',
              path: OV_PATHS.contentRead,
              query: { uri, offset: 0, limit: -1, raw: true },
            },
            signal,
          );
          return content
            .split('\n')
            .filter((line) => line.trim())
            .map(
              (line) =>
                JSON.parse(line) as Parameters<
                  typeof readNativeMessages
                >[0][number],
            );
        };
        const uri = meta.uri.replace(/\/+$/, '');
        const messages = await readMessages(`${uri}/messages.jsonl`).catch(
          (error) => {
            // Empty sessions may not have created their live JSONL yet.
            if (isOvClientError(error) && error.statusCode === 404) return [];
            throw error;
          },
        );
        const archives: Parameters<typeof readNativeMessages>[0][] = [];
        // Four concurrent archive reads per instance; preserve chronological order.
        for (let offset = 0; offset < meta.commit_count; offset += 4) {
          const batch = await Promise.all(
            Array.from(
              { length: Math.min(4, meta.commit_count - offset) },
              async (_, index) => {
                const archive = `archive_${String(offset + index + 1).padStart(3, '0')}`;
                return readMessages(`${uri}/history/${archive}/messages.jsonl`);
              },
            ),
          );
          archives.push(...batch);
        }
        const seen = new Set<string>();
        return readNativeMessages(
          [...archives.flat(), ...messages].filter((message) => {
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
    },
    client,
    (scoped) => createNativeBotService(scoped, options),
  );
}
