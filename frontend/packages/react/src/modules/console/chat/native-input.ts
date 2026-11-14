import { OvClientError } from '../../../client/errors.js';
import type { VikingBotChatInput } from './types.js';

/** Uploads belong to the host; native Bot accepts image URLs, not browser Files. */
export function nativeChatInput({
  sessionId,
  text,
  references,
  attachments,
}: VikingBotChatInput) {
  const images = attachments.map((attachment) => {
    if (
      !attachment.mimeType?.startsWith('image/') ||
      !attachment.previewUrl ||
      !/^(https:|data:image\/)/.test(attachment.previewUrl)
    ) {
      throw new OvClientError({
        code: 'BOT_INVALID_ATTACHMENT',
        message:
          'Upload images to an HTTPS or data URL before sending them to native VikingBot',
      });
    }
    return { type: 'image_url', image_url: { url: attachment.previewUrl } };
  });
  return {
    session_id: sessionId,
    message: [text, ...(references ?? []).map((reference) => reference.uri)]
      .filter(Boolean)
      .join('\n\n'),
    images,
    stream: true,
  };
}
