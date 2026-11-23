import type { VikingBotMessage } from './model.js';

const timestampSource = Symbol('vikingBotTimestampSource');
type DisplayMessage = VikingBotMessage & {
  [timestampSource]?: VikingBotMessage;
};

/** Keep display timestamps tied to their original metadata without changing row ids. */
export function messageTimestampSource(message: VikingBotMessage) {
  return (message as DisplayMessage)[timestampSource] ?? message;
}

export function retainMessageTimestampSource(
  message: VikingBotMessage,
  source: VikingBotMessage,
) {
  Object.defineProperty(message, timestampSource, {
    value: messageTimestampSource(source),
    configurable: true,
  });
}
