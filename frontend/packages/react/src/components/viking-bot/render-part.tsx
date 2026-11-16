import type { VikingBotLabels, VikingBotMessagePart } from './model.js';

/** 片段渲染所需的最小上下文。Context a part renderer needs. */
export interface PartContext {
  /** 内置文案。Built-in copy. */
  labels: VikingBotLabels;
  /** 打开资源引用。Opens a resource reference. */
  onOpenUri?: (uri: string) => void;
  /** 复制文本。Copies text. */
  onCopy?: (text: string) => void;
}

/** 渲染一个片段。Renders one part. */
export function renderPart(part: VikingBotMessagePart, context: PartContext) {
  const { labels, onOpenUri } = context;
  if (part.type === 'text') {
    return part.format === 'markdown' ? (
      <div className="ov-viking-bot__text ov-viking-bot__text--markdown">
        {part.text}
      </div>
    ) : (
      <div className="ov-viking-bot__text">{part.text}</div>
    );
  }
  if (part.type === 'reasoning') {
    return (
      <details className="ov-viking-bot__reasoning" open={part.running}>
        <summary>
          {labels.reasoning}
          {part.durationMs ? ` · ${Math.round(part.durationMs / 1000)}s` : ''}
        </summary>
        <div className="ov-viking-bot__reasoning-body">{part.text}</div>
      </details>
    );
  }
  if (part.type === 'iteration') {
    return (
      <div className="ov-viking-bot__iteration" role="separator">
        #{part.iteration}
      </div>
    );
  }
  if (part.type === 'context') {
    return (
      <button
        type="button"
        className="ov-viking-bot__context"
        title={part.abstract}
        onClick={() => onOpenUri?.(part.uri)}
      >
        {part.label ?? part.uri}
      </button>
    );
  }
  if (part.type === 'tool') {
    return (
      <details className="ov-viking-bot__tool" open={part.status === 'running'}>
        <summary>
          <span className="ov-viking-bot__tool-name">{part.name}</span>
          <span className="ov-viking-bot__tool-status">
            {labels[
              `tool${capitalize(part.status)}` as keyof VikingBotLabels
            ] ?? part.status}
          </span>
          {part.durationMs ? <span>{`${part.durationMs}ms`}</span> : null}
        </summary>
        {part.input ? (
          <pre className="ov-viking-bot__tool-input">
            {JSON.stringify(part.input, null, 2)}
          </pre>
        ) : null}
        {part.output ? (
          <pre className="ov-viking-bot__tool-output">{part.output}</pre>
        ) : null}
      </details>
    );
  }
  if (part.type === 'tool_result') {
    return (
      <pre
        className={
          part.isError
            ? 'ov-viking-bot__tool-output ov-viking-bot__tool-output--error'
            : 'ov-viking-bot__tool-output'
        }
      >
        {part.output}
      </pre>
    );
  }
  if (part.type === 'image') {
    return (
      <img
        className="ov-viking-bot__image"
        src={part.src}
        alt={part.alt ?? ''}
      />
    );
  }
  return (
    <button
      type="button"
      className="ov-viking-bot__attachment"
      onClick={() => part.uri && onOpenUri?.(part.uri)}
    >
      {part.name}
    </button>
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
