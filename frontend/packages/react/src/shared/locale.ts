/** Internal language resources shared by components and modules. */
export type Locale = 'en-US' | 'zh-CN';
export type LocaleMessages<T> = Record<Locale, T>;

/** Missing overrides inherit the selected language; empty strings stay explicit. */
export function mergeLabels<T extends object>(
  defaults: T,
  overrides?: Partial<T>,
): T {
  const result = { ...defaults };
  for (const key of Object.keys(overrides ?? {}) as (keyof T)[]) {
    const value = overrides?.[key];
    if (value != null) result[key] = value;
  }
  return result;
}

export function resolveLocale<T extends object>(
  messages: LocaleMessages<T>,
  locale: Locale = 'en-US',
  overrides?: Partial<T>,
): T {
  const defaults = messages[locale] ?? messages['en-US'];
  return overrides ? mergeLabels(defaults, overrides) : defaults;
}
