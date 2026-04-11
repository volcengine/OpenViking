import en from './locales/en'
import zhCN from './locales/zh-CN'

export const defaultLanguage = 'en' as const

export const resources = {
  en,
  'zh-CN': zhCN,
} as const

export type SupportedLanguage = keyof typeof resources

export const supportedLanguages = Object.keys(resources) as SupportedLanguage[]