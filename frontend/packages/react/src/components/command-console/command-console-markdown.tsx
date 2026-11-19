import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getCommandConsoleSafeHref } from './command-console-text.js';
import {
  COMMAND_CONSOLE_MARKDOWN_COMPONENTS,
  CommandConsoleMarkdownContext,
  type CommandConsoleMarkdownOptions,
} from './command-console-markdown-renderers.js';

export interface CommandConsoleMarkdownProps extends CommandConsoleMarkdownOptions {
  text: string;
}

/** Markdown uses stable renderers; callbacks and labels flow through the instance context. */
export function CommandConsoleMarkdown({
  text,
  ...options
}: CommandConsoleMarkdownProps) {
  return (
    <CommandConsoleMarkdownContext.Provider value={options}>
      <div className="ov-command-console__markdown">
        <ReactMarkdown
          remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
          urlTransform={(url) =>
            /^viking:/i.test(url)
              ? (getCommandConsoleSafeHref(url) ?? '')
              : defaultUrlTransform(url)
          }
          components={COMMAND_CONSOLE_MARKDOWN_COMPONENTS}
        >
          {text}
        </ReactMarkdown>
      </div>
    </CommandConsoleMarkdownContext.Provider>
  );
}
