import type { RefObject, UIEventHandler } from 'react';
import type { CommandConsoleInlineCompletion } from './command-completion.js';
import type { CommandConsoleCommandInputMatch } from './command-input.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';
import { getCommandConsoleArgumentHints } from './command-input.js';
import { CommandConsoleCommandText } from './command-console-command-text.js';

/** 原生 textarea 负责编辑与选区，镜像只绘制颜色和不属于输入值的参数提示。 */
export function CommandConsoleInputMirror({
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
  input: CommandConsoleCommandInputMatch | undefined;
  inline?: CommandConsoleInlineCompletion;
  colorInput?: boolean;
  onOpenUri?: (uri: string) => void;
  onScroll: UIEventHandler<HTMLSpanElement>;
  mirrorRef: RefObject<HTMLSpanElement | null>;
  hintId: string;
}) {
  const hints =
    input && !input.rest.trim()
      ? getCommandConsoleArgumentHints(input.command)
      : [];
  return (
    <>
      <span
        className="ov-command-console__input-mirror"
        ref={mirrorRef}
        onScroll={onScroll}
      >
        {input ? (
          <>
            {input.leadingWhitespace}
            <span aria-hidden="true">
              <span className="ov-command-console__input-command">
                <CommandConsoleCommandText
                  text={input.prefix}
                  name={input.command.name}
                />
              </span>
            </span>
            <CommandConsoleUriText
              text={input.rest}
              mode="literal"
              onOpenUri={onOpenUri}
              editor
            />
          </>
        ) : colorInput ? (
          <span
            className="ov-command-console__input-command"
            aria-hidden="true"
          >
            <CommandConsoleCommandText
              text={value}
              namespaceOnly={inline?.kind !== 'command'}
            />
          </span>
        ) : (
          <CommandConsoleUriText
            text={value}
            mode="literal"
            onOpenUri={onOpenUri}
            editor
          />
        )}
        <span aria-hidden="true">
          {inline ? (
            <span className="ov-command-console__input-completion">
              {inline.suffix}
            </span>
          ) : null}
          {hints.length ? (
            <span className="ov-command-console__input-arguments">
              {input?.rest.endsWith(' ') ? '' : ' '}
              {hints.map((hint) => hint.label).join(' ')}
            </span>
          ) : null}
          {value.endsWith('\n') ? '\u200b' : null}
        </span>
      </span>
      <span
        id={hintId}
        className="ov-command-console__input-hint-description"
        aria-live={inline?.kind === 'prefix' ? 'polite' : undefined}
      >
        {inline?.value}
        {hints.map((hint) => `${hint.label}: ${hint.description}`).join('; ')}
      </span>
    </>
  );
}
