import {
  compileCommand,
  parseArgs,
} from '#/features/workbench/compile/lib/commands'
import { describe, expect, it } from 'vitest'

describe('Compile form command utilities', () => {
  it('rejects JSON arguments that are not an object', () => {
    expect(() => parseArgs('[]')).toThrow()
  })
  it('never puts private arguments into copied commands', () => {
    expect(
      compileCommand({
        from: ['viking://resources/a'],
        to: 'viking://resources/b',
        skill: 'viking://agent/skills/s',
        args: { api_key: 'private' },
      }),
    ).not.toContain('private')
  })
})
