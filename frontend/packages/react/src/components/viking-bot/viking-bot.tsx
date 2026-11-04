import styles from './viking-bot.module.css';

export type {
  VikingBotAttachment,
  VikingBotAttachmentPart,
  VikingBotComposerProps,
  VikingBotComposerRef,
  VikingBotContextPart,
  VikingBotDraft,
  VikingBotImagePart,
  VikingBotIterationPart,
  VikingBotLabels,
  VikingBotMessage,
  VikingBotMessageListProps,
  VikingBotMessagePart,
  VikingBotProps,
  VikingBotReasoningPart,
  VikingBotStreamingState,
  VikingBotTextPart,
  VikingBotToolPart,
  VikingBotToolResultPart,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function VikingBot() {
  return <div className={styles.root}>VikingBot</div>;
}
