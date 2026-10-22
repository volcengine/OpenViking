import { ESLint, Linter } from 'eslint'
import { describe, expect, it } from 'vitest'
import plugin from './eslint-i18n.mjs'

function check(code: string, rule: string) {
  return new Linter().verify(code, [
    {
      languageOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        parserOptions: { ecmaFeatures: { jsx: true } },
      },
      plugins: { 'studio-i18n': plugin },
      rules: { [`studio-i18n/${rule}`]: 'error' },
    },
  ])
}

describe('i18n lint boundaries', () => {
  it.each([
    'throw new Error("Failed to save")',
    'toast.error(`Could not save ${name}`)',
    'toast("Upload failed")',
    'toast(t("failed"), { description: "Try again" })',
    'const message = "Upload failed"; toast(message)',
    'toast.success(ok ? "Saved" : "Skipped")',
    'toast.error(t("failed"), { description: "Try again" })',
    'const message = "Failed to save"; throw new Error(message)',
    'const first = "Failed"; const second = first; toast.error(second)',
    'new OvClientError({ code: "FAILED", message: "Failed to save" })',
    'setError("Invalid value")',
    'const v = <span>{`Found ${count} results`}</span>',
    'const v = <button aria-label={`Delete ${name}`} />',
  ])('rejects a hardcoded message: %s', (code) => {
    const messages = check(code, 'no-literal-message')
    expect(
      messages.some((m) => m.ruleId === 'studio-i18n/no-literal-message'),
    ).toBe(true)
  })

  it('reports both hardcoded title and description in a direct toast call', () => {
    const messages = check(
      'toast("Upload failed", { description: "Try again" })',
      'no-literal-message',
    )
    expect(messages.map((message) => message.ruleId)).toEqual([
      'studio-i18n/no-literal-message',
      'studio-i18n/no-literal-message',
    ])
  })

  it.each([
    'throw new Error(t("failed"))',
    'toast.error(error.message)',
    'toast(t("saved"), { description: t("details") })',
    'toast(error.message)',
    'toast.dismiss("upload-id")',
    'const id = "upload-id"; toast.dismiss(id)',
    'toast["dismiss"]("upload-id")',
    'const a = "first"; const b = "second"; const v = <span>{`${a} · ${b}`}</span>',
    'const v = <div className={`grid ${className}`} />',
    'new DOMException("Aborted", "AbortError")',
    'const command = "ov status"',
    'const message = t("failed"); toast.error(message)',
  ])(
    'preserves translated text, raw diagnostics and protocol strings: %s',
    (code) => {
      expect(check(code, 'no-literal-message')).toEqual([])
    },
  )

  it.each([
    'date.toLocaleString()',
    'date.toLocaleDateString(undefined, options)',
    'date.toLocaleString([], options)',
    'new Intl.NumberFormat()',
    'Intl.DateTimeFormat()',
  ])('requires an explicit display locale: %s', (code) => {
    expect(check(code, 'explicit-locale')).toHaveLength(1)
  })

  it.each([
    'date.toLocaleString(i18n.resolvedLanguage)',
    'new Intl.DateTimeFormat(locale, options)',
    'text.toLocaleLowerCase()',
    'formatDateTime(date)',
  ])('accepts explicit locale or unrelated methods: %s', (code) => {
    expect(check(code, 'explicit-locale')).toEqual([])
  })

  it.each([
    'src/components/ui/dialog.tsx',
    'src/routes/__root.tsx',
    'src/lib/clipboard.ts',
  ])(
    'enforces i18n in the actual project config: %s',
    async (filePath) => {
      const jsx = filePath.endsWith('.tsx')
      const code = jsx
        ? 'export const value = <button title="Delete item">Delete</button>'
        : 'throw new Error("Failed to save")'
      const config: Linter.Config = await new ESLint().calculateConfigForFile(
        filePath,
      )
      const rule = jsx
        ? 'i18next/no-literal-string'
        : 'studio-i18n/no-literal-message'
      const rules = config.rules ?? {}
      const setting = rules[rule]
      expect(Array.isArray(setting) ? setting[0] : setting).toBe(2)
      // Exercise the configured i18n rules without building an unrelated full
      // TypeScript program for each synthetic snippet.
      const messages = new Linter().verify(
        code,
        [
          {
            files: ['**/*.{ts,tsx}'],
            languageOptions: {
              parser: config.languageOptions?.parser,
              parserOptions: { ecmaFeatures: { jsx: true } },
            },
            plugins: config.plugins,
            rules: Object.fromEntries(
              Object.entries(rules).filter(
                ([key]) =>
                  key.startsWith('i18next/') || key.startsWith('studio-i18n/'),
              ),
            ),
          },
        ],
        { filename: filePath },
      )
      expect(
        messages.filter((m) => m.ruleId === rule && m.severity === 2).length,
        JSON.stringify(messages),
      ).toBeGreaterThan(0)
    },
    15000,
  )
})
