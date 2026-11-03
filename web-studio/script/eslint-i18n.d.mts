import type { Rule } from 'eslint'

export const noLiteralMessage: Rule.RuleModule
export const explicitLocale: Rule.RuleModule

declare const plugin: { rules: Record<string, Rule.RuleModule> }
export default plugin
