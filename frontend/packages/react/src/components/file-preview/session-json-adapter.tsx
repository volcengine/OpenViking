import { json } from '@ark-design/ui/file-viewer/adapters/json';
import type {
  Adapter,
  ContentRendererProps,
} from '@ark-design/ui/file-viewer/core';
import { parseSessionMessages } from './session-messages.js';
import type { SessionPreviewMessage } from './session-messages.js';

interface SessionPrepared {
  messages?: readonly SessionPreviewMessage[];
  original: unknown;
}

/** Preserve JSON admission, managed reads and the original source renderer. */
export function sessionJsonAdapter(enabled: boolean): Adapter {
  const fallback = json({ defaultExpandDepth: 4 });
  if (!enabled) return fallback;
  return (context) => {
    const descriptor = fallback(context);
    return {
      ...descriptor,
      prepare: async (source, prepareContext) => {
        const original = await descriptor.prepare?.(source, prepareContext);
        prepareContext.signal.throwIfAborted();
        if (
          prepareContext.admission?.mode === 'partial' ||
          !('getText' in source)
        )
          return { original };
        const content = await source.getText();
        prepareContext.signal.throwIfAborted();
        return { original, messages: parseSessionMessages(content) };
      },
      disposePrepared: descriptor.disposePrepared
        ? (prepared) =>
            descriptor.disposePrepared?.((prepared as SessionPrepared).original)
        : undefined,
      load: async (loadContext) => {
        const [{ Component: Json }, { SessionMessagesView }] =
          await Promise.all([
            descriptor.load(loadContext),
            import('./session-messages-view.js'),
          ]);
        loadContext.signal.throwIfAborted();
        return {
          Component: (props: ContentRendererProps<SessionPrepared>) =>
            props.viewMode !== 'source' && props.prepared?.messages ? (
              <SessionMessagesView
                messages={props.prepared.messages}
                locale={props.locale}
              />
            ) : (
              <Json {...props} prepared={props.prepared?.original} />
            ),
        };
      },
    };
  };
}
