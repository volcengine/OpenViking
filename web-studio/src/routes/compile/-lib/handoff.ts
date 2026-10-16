import type { CompileRequest } from './api'
import { parseArgs, tokenize } from './commands'

let handoff: { scope: string; request: Partial<CompileRequest> } | undefined
export const readCompileHandoff = (scope: string) =>
  handoff?.scope === scope ? handoff.request : undefined
export const clearCompileHandoff = () => {
  handoff = undefined
}
export function prepareCompileHandoff(scope: string, input: string) {
  const request: Partial<CompileRequest> = { from: [] }
  try {
    const tokens = tokenize(input)
    if (tokens[0] !== 'compile') {
      handoff = undefined
      return
    }
    for (let i = 1; i + 1 < tokens.length; i += 2) {
      const key = tokens[i],
        value = tokens[i + 1]
      if (key === '--from')
        request.from?.push(...value.split(',').filter(Boolean))
      if (key === '--to') request.to = value
      if (key === '--skill') request.skill = value
      if (key === '--instruction') request.instruction = value
      if (key === '--args') request.args = parseArgs(value)
    }
  } catch {
    /* Keep only successfully parsed, in-memory values. */
  }
  handoff = { scope, request }
}
