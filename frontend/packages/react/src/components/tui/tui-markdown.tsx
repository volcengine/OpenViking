import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getTuiSafeHref } from './tui-text.js';
import {
  TUI_MARKDOWN_COMPONENTS,
  TuiMarkdownContext,
  type TuiMarkdownOptions,
} from './tui-markdown-renderers.js';

export interface TuiMarkdownProps extends TuiMarkdownOptions {
  text: string;
}

/** Markdown uses stable renderers; callbacks and labels flow through the instance context. */
export function TuiMarkdown({ text, ...options }: TuiMarkdownProps) {
  return (
    <TuiMarkdownContext.Provider value={options}>
      <div className="ov-tui__markdown">
        <ReactMarkdown
          remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
          urlTransform={(url) =>
            /^viking:/i.test(url)
              ? (getTuiSafeHref(url) ?? '')
              : defaultUrlTransform(url)
          }
          components={TUI_MARKDOWN_COMPONENTS}
        >
          {text}
        </ReactMarkdown>
      </div>
    </TuiMarkdownContext.Provider>
  );
}
