import type { CompileRequest } from '#/features/workbench/compile/lib/api'

export function parseArgs(value: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new TypeError('Expected a JSON object')
  }
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
