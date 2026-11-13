import type { renderErrors as EnglishRenderErrors } from './en-US.js';

export const renderErrors = {
  dashboard: '经验看板显示失败',
} satisfies Record<keyof typeof EnglishRenderErrors, string>;

export default renderErrors;
