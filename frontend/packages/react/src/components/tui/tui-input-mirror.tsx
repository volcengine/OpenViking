import type { RefObject, UIEventHandler } from 'react';
import type { TuiInlineCompletion } from './command-completion.js';
import type { TuiCommandInputMatch } from './command-input.js';
import { TuiUriText } from './tui-uri-text.js';
import { getTuiArgumentHints } from './command-input.js';
import { TuiCommandText } from './tui-command-text.js';

/** 原生 textarea 负责编辑与选区，镜像只绘制颜色和不属于输入值的参数提示。 */
export function TuiInputMirror({
  value,
  input,
  inline,
  colorInput,
  onOpenUri,
  onScroll,
  mirrorRef,
  hintId,
}: {
  value: string;
  input: TuiCommandInputMatch | undefined;
  inline?: TuiInlineCompletion;
  colorInput?: boolean;
  onOpenUri?: (uri: string) => void;
  onScroll: UIEventHandler<HTMLSpanElement>;
  mirrorRef: RefObject<HTMLSpanElement | null>;
  hintId: string;
}) {
  const hints =
    input && !input.rest.trim() ? getTuiArgumentHints(input.command) : [];
  return (
    <>
      <span
        className="ov-tui__input-mirror"
        ref={mirrorRef}
        onScroll={onScroll}
      >
        {input ? (
          <>
            {input.leadingWhitespace}
            <span aria-hidden="true">
              <span className="ov-tui__input-command">
                <TuiCommandText text={input.prefix} name={input.command.name} />
              </span>
            </span>
            <TuiUriText
              text={input.rest}
              mode="literal"
              onOpenUri={onOpenUri}
              editor
            />
          </>
        ) : colorInput ? (
          <span className="ov-tui__input-command" aria-hidden="true">
            <TuiCommandText
              text={value}
              namespaceOnly={inline?.kind !== 'command'}
            />
          </span>
        ) : (
          <TuiUriText
            text={value}
            mode="literal"
            onOpenUri={onOpenUri}
            editor
          />
        )}
        <span aria-hidden="true">
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
