/** Internal language resources shared by components and modules. */
export type Locale = 'en-US' | 'zh-CN';
export type LocaleMessages<T> = Record<Locale, T>;

/** 是否是应当逐键合并的普通对象（数组按值整体替换）。Whether a value is a plain object to merge key by key. */
const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * 逐键合并文案。嵌套对象递归合并——覆盖一个子项不能丢掉同组的其他默认值；
 * 数组与函数按值整体替换，`undefined` 继承默认值，空字符串保留为显式覆盖。
 *
 * Merges copy key by key. Nested objects merge recursively — overriding one child must
 * not drop its siblings' defaults — while arrays and functions replace wholesale,
 * `undefined` inherits the default and an empty string stays an explicit override.
 */
export function mergeLabels<T extends object>(
  defaults: T,
  overrides?: Partial<T>,
): T {
  const result = { ...defaults };
  for (const key of Object.keys(overrides ?? {}) as (keyof T)[]) {
    const value = overrides?.[key];
    if (value == null) continue;
    const base = defaults[key];
    result[key] =
      isPlainObject(value) && isPlainObject(base)
        ? mergeLabels(base, value)
        : value;
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
