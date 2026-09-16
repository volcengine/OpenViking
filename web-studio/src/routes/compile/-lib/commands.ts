import type { CompileRequest } from './api'

/** Tokenize the documented CLI subset, without executing shell syntax. */
export function tokenize(input: string): string[] {
  const tokens: string[] = []
  let value = '',
    quote = '',
    escaped = false,
    started = false
  for (const c of input.trim()) {
    if (escaped) {
      value += c
      escaped = false
      started = true
      continue
    }
    if (c === '\\' && quote !== "'") {
      escaped = true
      continue
    }
    if (quote) {
      if (c === quote) quote = ''
      else value += c
      continue
    }
    if (c === '"' || c === "'") {
      quote = c
      started = true
      continue
    }
    if (/\s/.test(c)) {
      if (started) tokens.push(value)
      value = ''
      started = false
      continue
    }
    if ('|;&<>`'.includes(c) || c === '$')
      throw new Error('Unsupported shell syntax')
    value += c
    started = true
  }
  if (quote || escaped) throw new Error('Unclosed quote or escape')
  if (started) tokens.push(value)
  const result = tokens[0] === 'ov' ? tokens.slice(1) : tokens
  if (result[0]?.startsWith('/')) result[0] = result[0].slice(1)
  return result
}
export function parseCompile(tokens: string[]): CompileRequest {
  const result: CompileRequest = { from: [], to: '', skill: '' }
  const seen = new Set<string>()
  for (let i = 1; i < tokens.length; i += 2) {
    const key = tokens[i],
      value = tokens[i + 1]
    if (
      !['--from', '--to', '--skill', '--instruction', '--args'].includes(key) ||
      i + 1 >= tokens.length
    )
      throw new Error(`Invalid argument: ${key}`)
    if (key !== '--from' && seen.has(key))
      throw new Error(`Duplicate argument: ${key}`)
    seen.add(key)
    if (key === '--from') result.from.push(...value.split(','))
    else if (key === '--args') result.args = parseArgs(value)
    else if (key === '--to') result.to = value
    else if (key === '--skill') result.skill = value
    else result.instruction = value
  }
  if (
    !result.from.length ||
    result.from.some((v) => !v.trim()) ||
    !result.to ||
    !result.skill
  )
    throw new Error('Required: --from, --to, --skill')
  result.from = [...new Set(result.from.map((v) => v.trim()))]
  return result
}
export function parseArgs(value: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error('--args must be a JSON object')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    throw new Error('--args must be a JSON object')
  return parsed as Record<string, unknown>
}
const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`
export function compileCommand(request: CompileRequest): string {
  return [
    'ov compile',
    ...request.from.map((uri) => `--from ${quote(uri)}`),
    `--to ${quote(request.to)}`,
    `--skill ${quote(request.skill)}`,
    ...(request.instruction
      ? [`--instruction ${quote(request.instruction)}`]
      : []),
  ].join(' ')
}
export const isCompileCommand = (input: string) =>
  /^(?:ov\s+)?(?:\/?compile|\/?task)(?:\s|$)/.test(input.trim())
