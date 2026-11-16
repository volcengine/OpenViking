interface MarkdownNode {
  type: string;
  value?: string;
  children?: MarkdownNode[];
}

/** 正文及行内代码只显示脱敏值；代码块由专用视图处理占位符。 */
export function remarkMaskApiKey({ keyToken }: { keyToken: string }) {
  return (tree: MarkdownNode) => {
    function mask(node: MarkdownNode) {
      if (
        (node.type === 'text' ||
          node.type === 'inlineCode' ||
          node.type === 'html') &&
        node.value
      ) {
        node.value = node.value.split(keyToken).join('••••••••');
      }
      node.children?.forEach(mask);
    }
    mask(tree);
  };
}
