import type { RefObject } from 'react';
import type { TuiInlineCompletion } from './command-completion.js';
import type { TuiCommandInputMatch } from './command-input.js';
import { getTuiArgumentHints } from './command-input.js';

/** 原生 textarea 负责编辑与选区，镜像只绘制颜色和不属于输入值的参数提示。 */
export function TuiInputMirror({
  value,
  input,
  inline,
  colorInput,
  mirrorRef,
  hintId,
}: {
  value: string;
  input: TuiCommandInputMatch | undefined;
  inline?: TuiInlineCompletion;
  colorInput?: boolean;
  mirrorRef: RefObject<HTMLSpanElement | null>;
  hintId: string;
}) {
  const hints =
    input && !input.rest.trim() ? getTuiArgumentHints(input.command) : [];
  return (
    <>
      <span className="ov-tui__input-mirror" ref={mirrorRef} aria-hidden="true">
        {input ? (
          <>
            {input.leadingWhitespace}
            <span className="ov-tui__input-command">{input.prefix}</span>
            {input.rest}
          </>
        ) : colorInput ? (
          <span className="ov-tui__input-command">{value}</span>
        ) : (
          value
        )}
        {inline ? (
          <span className="ov-tui__input-completion">{inline.suffix}</span>
        ) : null}
        {hints.length ? (
          <span className="ov-tui__input-arguments">
            {input?.rest.endsWith(' ') ? '' : ' '}
            {hints.map((hint) => hint.label).join(' ')}
          </span>
        ) : null}
        {value.endsWith('\n') ? '\u200b' : null}
      </span>
      <span
        id={hintId}
        className="ov-tui__input-hint-description"
        aria-live={inline?.kind === 'prefix' ? 'polite' : undefined}
      >
        {inline?.value}
        {hints.map((hint) => `${hint.label}: ${hint.description}`).join('; ')}
      </span>
    </>
  );
}
