import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionFields,
  VikingBotInteractionRequest,
} from '../../../components/viking-bot/index.js';

export interface ConsoleHitlCard {
  /** Tool call ID followed by the zero-based card index. */
  id: string;
  cardType: string;
  title: string;
  payload: Readonly<Record<string, unknown>>;
}

export interface ConsoleHitlCardOptions {
  fields?: VikingBotInteractionFields;
  /** Overrides a built-in card, or registers a new card type. */
  createRequest?: (card: ConsoleHitlCard) => VikingBotInteractionRequest;
  /** Converts a custom answer to the tool's value schema. */
  serialize?: (
    answer: VikingBotInteractionAnswer,
    card: ConsoleHitlCard,
  ) => unknown;
  /** Readable value used by the bubble and copy action, including history. */
  summarize?: (value: unknown, card: ConsoleHitlCard) => string;
}

export interface ConsoleHitlOptions {
  cards?: Readonly<Record<string, ConsoleHitlCardOptions>>;
  /** Distinguishes cards with the same type using their public ID and payload. */
  configureCard?: (card: ConsoleHitlCard) => ConsoleHitlCardOptions | undefined;
}

export interface HitlCard extends ConsoleHitlCard {
  request: VikingBotInteractionRequest;
  options?: ConsoleHitlCardOptions;
}
export interface HitlGroup {
  id: string;
  cards: readonly HitlCard[];
}
