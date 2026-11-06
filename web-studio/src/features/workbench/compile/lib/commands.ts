import type {
  CompileCreateRequest,
  CompileExecutionRequest,
  CompileRequest,
} from '#/features/workbench/compile/lib/api'

export class CompileCommandError extends Error {
  constructor(
    readonly code:
      | 'shellSyntax'
      | 'unclosedQuote'
      | 'invalidArgument'
      | 'duplicateArgument'
      | 'required'
      | 'argsObject'
      | 'unknownCommand'
      | 'taskUsage'
      | 'missingValue'
      | 'listUsage'
      | 'pendingSubmission'
      | 'expiredSubmission',
  ) {
    super(code)
    this.name = 'CompileCommandError'
  }
}

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
      throw new CompileCommandError('shellSyntax')
    value += c
    started = true
  }
  if (quote || escaped) throw new CompileCommandError('unclosedQuote')
  if (started) tokens.push(value)
  const result = tokens[0] === 'ov' ? tokens.slice(1) : tokens
  if (result[0]?.startsWith('/')) result[0] = result[0].slice(1)
  return result
}
/** Return whether an unmodified name has 1–64 ASCII letters, digits, underscores, or hyphens. */
export function isValidCompileName(value: string): boolean {
  return (
    value.length >= 1 && value.length <= 64 && !/[^A-Za-z0-9_-]/.test(value)
  )
}

export function parseCompile(
  tokens: string[],
): CompileExecutionRequest | CompileCreateRequest {
  if (tokens[1] === '--compile-name') {
    if (tokens.length !== 3 || !isValidCompileName(tokens[2]))
      throw new CompileCommandError('invalidArgument')
    return { compile_name: tokens[2] }
  }
  const create = tokens[1] === 'create'
  const result: CompileRequest & { compile_name?: string } = {
    from: [],
    to: '',
    skill: '',
  }
  const seen = new Set<string>()
  for (let i = tokens[1] === 'create' ? 2 : 1; i < tokens.length; i += 2) {
    const key = tokens[i],
      value = tokens[i + 1]
    if (
      ![
        '--from',
        '--to',
        '--skill',
        '--instruction',
        '--args',
        ...(create ? ['--compile-name'] : []),
      ].includes(key) ||
      i + 1 >= tokens.length
    )
      throw new CompileCommandError('invalidArgument')
    if (key !== '--from' && seen.has(key))
      throw new CompileCommandError('duplicateArgument')
    seen.add(key)
    if (key === '--compile-name') {
      if (!isValidCompileName(value))
        throw new CompileCommandError('invalidArgument')
      result.compile_name = value
    } else if (key === '--from') result.from.push(...value.split(','))
    else if (key === '--args') result.args = parseArgs(value)
    else if (key === '--to') result.to = value
    else if (key === '--skill') result.skill = value
    else result.instruction = value
  }
  if (
    (create && !result.compile_name) ||
    !result.from.length ||
    result.from.some((v) => !v.trim()) ||
    !result.to ||
    !result.skill
  )
    throw new CompileCommandError('required')
  result.from = [...new Set(result.from.map((v) => v.trim()))]
  return result
}
export function parseArgs(value: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new CompileCommandError('argsObject')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    throw new CompileCommandError('argsObject')
  return parsed as Record<string, unknown>
}
const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`
export function compileCommand(
  request: CompileExecutionRequest | CompileCreateRequest,
  create = false,
): string {
  if (!('from' in request))
    return `ov compile --compile-name ${quote(request.compile_name)}`
  return [
    create ? 'ov compile create' : 'ov compile',
    ...(create && 'compile_name' in request
      ? ['--compile-name', quote(request.compile_name)]
      : []),
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

/** Persist only parsed public fields; malformed input must never echo raw args. */
export function compileHistory(input: string): {
  title: string
  remember: boolean
} {
  try {
    const tokens = tokenize(input)
    if (tokens[0] === 'compile') {
      const request = parseCompile(tokens)
      return {
        title: compileCommand(request, tokens[1] === 'create'),
        remember: !tokens.includes('--args'),
      }
    }
    if (
      tokens[0] === 'task' &&
      !tokens.some((token) => token.startsWith('--args'))
    )
      return { title: input, remember: true }
    return { title: 'task', remember: false }
  } catch {
    return { title: 'compile / task', remember: false }
  }
}
