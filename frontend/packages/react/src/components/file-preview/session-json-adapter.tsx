import { json } from '@ark-design/ui/file-viewer/adapters/json';
import type {
  Adapter,
  ContentRendererProps,
} from '@ark-design/ui/file-viewer/core';
import { parseSessionMessages } from './session-messages.js';
import type { SessionPreviewMessage } from './session-messages.js';
import { createJsonPreviewModel } from './json-preview-model.js';
import type { JsonPreviewModel } from './json-preview-model.js';

interface SessionPrepared {
  messages?: readonly SessionPreviewMessage[];
  original: unknown;
  preview?: JsonPreviewModel;
}

/** Preserve JSON admission, managed reads and the original source renderer. */
export function sessionJsonAdapter(enabled: boolean): Adapter {
  const fallback = json({ defaultExpandDepth: 4 });
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
        return {
          original,
          messages: enabled ? parseSessionMessages(content) : undefined,
          preview: createJsonPreviewModel(content),
        };
      },
      disposePrepared: descriptor.disposePrepared
        ? (prepared) =>
            descriptor.disposePrepared?.((prepared as SessionPrepared).original)
        : undefined,
      load: async (loadContext) => {
        const [{ Component: Json }, { JsonPreview }, { SessionMessagesView }] =
          await Promise.all([
            descriptor.load(loadContext),
            import('./json-preview.js'),
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
            ) : props.viewMode !== 'source' &&
              !enabled &&
              props.fileType === 'json' &&
              props.fileName?.toLowerCase().endsWith('.json') === true &&
              props.prepared?.preview ? (
              <JsonPreview {...props} model={props.prepared.preview} />
            ) : (
              <Json {...props} prepared={props.prepared?.original} />
            ),
        };
      },
    };
  };
}
