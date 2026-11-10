import { resolveLocale, type Locale } from '../../../shared/locale.js';
import type {
  FileBrowserAclLabels,
  FileBrowserAclLevel,
} from '../acl-types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
export type AclText = Required<FileBrowserAclLabels>;
export const aclLabels = (locale: Locale, labels?: FileBrowserAclLabels) =>
  resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
export const levelLabel = (level: FileBrowserAclLevel, text: AclText) =>
  level === 'manage' ? text.manageLevel : text[level];
