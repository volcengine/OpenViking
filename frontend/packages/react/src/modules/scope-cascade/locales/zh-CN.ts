import type { renderErrors as EnglishRenderErrors } from './en-US.js';
import type enUS from './en-US.js';

const messages: typeof enUS = {
  levelNames: ['知识库', '数据空间', '用户'] as [string, string, string],
  error: '选项加载失败',
  retry: '重试',
};
export default messages;

export const renderErrors = {
  selector: '作用域选择器显示失败',
} satisfies Record<keyof typeof EnglishRenderErrors, string>;
