import { Fragment } from 'react';

/** 本地指南的有限 Markdown 子集；不执行 HTML，也不自动加载截图或外链。 */
function Inline({ text }: { text: string }) {
  return text
    .split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)]+\))/g)
    .map((part, index) => {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/.exec(part);
      if (link)
        return (
          <a
            key={index}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer"
          >
            {link[1]}
          </a>
        );
      if (part.startsWith('`'))
        return <code key={index}>{part.slice(1, -1)}</code>;
      if (part.startsWith('**'))
        return <strong key={index}>{part.slice(2, -2)}</strong>;
      return <Fragment key={index}>{part}</Fragment>;
    });
}

function Prose({ text }: { text: string }) {
  // 截图属于原产品说明，当前接入视图保留文字步骤，不发起截图请求。
  const lines = text.split('\n').filter((line) => !/^\s*!\[/.test(line));
  const blocks: string[] = [];
  let current = '';
  for (const line of lines) {
    if (!line.trim()) {
      if (current) blocks.push(current);
      current = '';
    } else if (/^#{1,6}\s/.test(line)) {
      if (current) blocks.push(current);
      blocks.push(line);
      current = '';
    } else {
      current += `${current ? '\n' : ''}${line}`;
    }
  }
  if (current) blocks.push(current);
  return blocks.map((block, index) => {
    if (/^#{1,6}\s/.test(block)) {
      return (
        <h5 key={index}>
          <Inline text={block.replace(/^#{1,6}\s/, '')} />
        </h5>
      );
    }
    if (block.startsWith('|')) {
      const rows = block
        .split('\n')
        .filter((row) => !/^\|[\s:|-]+\|$/.test(row));
      return (
        <table key={index}>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row
                  .split('|')
                  .slice(1, -1)
                  .map((cell, column) =>
                    rowIndex === 0 ? (
                      <th key={column}>
                        <Inline text={cell.trim()} />
                      </th>
                    ) : (
                      <td key={column}>
                        <Inline text={cell.trim()} />
                      </td>
                    ),
                  )}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
    return (
      <p key={index}>
        <Inline text={block} />
      </p>
    );
  });
}

export function AgentConnectMarkdown({ text }: { text: string }) {
  return (
    <div className="ov-agent-connect__markdown">
      {text
        .split(/(^[ \t]*```[^\n]*\n[\s\S]*?^[ \t]*```[ \t]*$)/gm)
        .map((part, index) => {
          const code = /^[ \t]*```[^\n]*\n([\s\S]*?)\n[ \t]*```[ \t]*$/.exec(
            part,
          );
          return code ? (
            <pre key={index}>
              <code>{code[1]}</code>
            </pre>
          ) : (
            <Prose key={index} text={part} />
          );
        })}
    </div>
  );
}
