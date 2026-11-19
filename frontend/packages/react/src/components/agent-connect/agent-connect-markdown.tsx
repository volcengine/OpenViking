import ReactMarkdown, { type Components } from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { type Options } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import {
  Children,
  createContext,
  isValidElement,
  useContext,
  type ReactNode,
} from 'react';
import {
  AgentConnectCodeBlock,
  type CodeBlockControls,
} from './agent-connect-code-block.js';
import { remarkMaskApiKey } from './agent-connect-mask.js';

// 与参考接入指南采用相同的 HTML 白名单；截图不属于当前接入视图。
const markdownHtmlSchema: Options = {
  attributes: {
    a: ['href', 'title'],
    code: [['className', /^language-[\w-]+$/]],
    details: ['open'],
    td: ['align', 'colSpan', 'rowSpan'],
    th: ['align', 'colSpan', 'rowSpan'],
  },
  protocols: { href: ['http', 'https', 'mailto'] },
  tagNames: [
    'a',
    'b',
    'blockquote',
    'br',
    'code',
    'del',
    'details',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'hr',
    'li',
    'ol',
    'p',
    'pre',
    's',
    'strong',
    'summary',
    'table',
    'tbody',
    'td',
    'th',
    'thead',
    'tr',
    'ul',
  ],
};

// 保持指令卡片原有标题尺寸，链接仍由用户主动打开。
const components: Components = {
  h1: ({ children }) => <h5>{children}</h5>,
  h2: ({ children }) => <h5>{children}</h5>,
  h3: ({ children }) => <h5>{children}</h5>,
  h4: ({ children }) => <h5>{children}</h5>,
  h5: ({ children }) => <h5>{children}</h5>,
  h6: ({ children }) => <h5>{children}</h5>,
  a: ({ children, href, title }) => (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  pre: MarkdownCodeBlock,
};

/** 先解析原始 HTML，再清理不允许的标签、属性与协议。 */
interface MarkdownProps extends CodeBlockControls {
  text: string;
  keyToken: string;
  apiKey: string;
}

const codeBlockContext = createContext<Omit<MarkdownProps, 'text'> | null>(
  null,
);

/** 稳定的渲染器身份保留代码块的显示状态及异步复制生命周期。 */
function MarkdownCodeBlock({ children }: { children?: ReactNode }) {
  const context = useContext(codeBlockContext);
  const child = Children.toArray(children)[0];
  if (
    !context ||
    !isValidElement<{ children?: string; className?: string }>(child) ||
    typeof child.props.children !== 'string'
  ) {
    return <pre>{children}</pre>;
  }
  const { keyToken, apiKey, ...controls } = context;
  const code = child.props.children.replace(/\n$/, '');
  return (
    <AgentConnectCodeBlock
      {...controls}
      language={child.props.className?.replace(/^language-/, '')}
      display={code.split(keyToken).join('••••••••')}
      copyText={code.split(keyToken).join(apiKey)}
    />
  );
}

export function AgentConnectMarkdown({ text, ...context }: MarkdownProps) {
  return (
    <div className="ov-agent-connect__markdown">
      <codeBlockContext.Provider value={context}>
        <ReactMarkdown
          remarkPlugins={[
            [remarkGfm, { singleTilde: false }],
            [remarkMaskApiKey, { keyToken: context.keyToken }],
          ]}
          rehypePlugins={[rehypeRaw, [rehypeSanitize, markdownHtmlSchema]]}
          components={components}
        >
          {text}
        </ReactMarkdown>
      </codeBlockContext.Provider>
    </div>
  );
}
