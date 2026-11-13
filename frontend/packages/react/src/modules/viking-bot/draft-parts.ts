import type {
  VikingBotDraft,
  VikingBotMessagePart,
} from '../../components/viking-bot/model.js';

export function draftParts(draft: VikingBotDraft): VikingBotMessagePart[] {
  return [
    ...(draft.text
      ? [{ type: 'text' as const, text: draft.text, format: 'plain' as const }]
      : []),
    ...draft.attachments.map((attachment): VikingBotMessagePart =>
      attachment.mimeType?.startsWith('image/') && attachment.previewUrl
        ? {
            type: 'image',
            src: attachment.previewUrl,
            alt: attachment.name,
          }
        : {
            type: 'attachment',
            name: attachment.name,
            mimeType: attachment.mimeType,
            previewUrl: attachment.previewUrl,
          },
    ),
    ...(draft.references ?? []).map((reference): VikingBotMessagePart => ({
      type: 'context',
      uri: reference.uri,
      contextType: reference.uri.includes('/memories/')
        ? 'memory'
        : reference.uri.includes('/skills/')
          ? 'skill'
          : 'resource',
    })),
  ];
}
