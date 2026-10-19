import i18n from './core'

export function formatNumber(value: number): string {
  return value.toLocaleString(i18n.resolvedLanguage ?? i18n.language)
}

export function formatDateTime(
  value: Date,
  options?: Intl.DateTimeFormatOptions,
): string {
  return value.toLocaleString(i18n.resolvedLanguage ?? i18n.language, options)
}
