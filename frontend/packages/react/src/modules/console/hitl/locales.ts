import type { Locale } from '../../../shared/locale.js';

const enUS = {
  empty: 'Not provided',
  uriLines: 'Enter one directory URI per line.',
  required: 'Complete this field',
  uriInvalid: 'Enter an OpenViking URI beginning with viking://',
  weekly: (weekday: string, time: string) => `Every ${weekday} ${time}`,
  daily: (time: string) => `Daily ${time}`,
};
const zhCN: typeof enUS = {
  empty: '未填写',
  uriLines: '每行填写一个目录 URI。',
  required: '请填写此项',
  uriInvalid: '请输入以 viking:// 开头的 OpenViking URI',
  weekly: (weekday, time) => `每${weekday} ${time}`,
  daily: (time) => `每日 ${time}`,
};
export const hitlLabels = (locale: Locale) =>
  locale === 'zh-CN' ? zhCN : enUS;
