import { describe, expect, it } from 'vitest'
import { compileCommand, parseArgs, parseCompile, tokenize } from './commands'

import { compileSuggestions } from './suggestions'
import {
  clearCompileHandoff,
  prepareCompileHandoff,
  readCompileHandoff,
} from './handoff'

describe('Compile command grammar', () => {
  it('round trips paths, quotes, spaces and unicode without shell execution', () => {
    const request = {
      from: ['viking://resources/团队 资料', "viking://resources/John's"],
      to: 'viking://resources/wiki',
      skill: 'viking://agent/skills/wiki',
      instruction: 'Keep "quotes" and $literal `text`',
    }
    expect(parseCompile(tokenize(compileCommand(request)))).toEqual(request)
  })
  it('accepts repeated sources and quoted JSON', () => {
    expect(
      parseCompile(
        tokenize(
          `compile --from viking://resources/a --from viking://resources/b --to viking://resources/c --skill viking://agent/skills/wiki --args '{"model_name":"hello world"}'`,
        ),
      ).args,
    ).toEqual({ model_name: 'hello world' })
  })
  it('rejects incomplete flags and shell operators', () => {
    expect(() => parseCompile(tokenize('compile --from'))).toThrow()
    expect(() => tokenize('compile | sh')).toThrow()
    expect(() => tokenize('compile "unfinished')).toThrow()
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

it('completes a Skill URI and keeps handoff scoped to the identity', () => {
  expect(
    compileSuggestions('compile --skill ', [], ['viking://agent/skills/wiki']),
  ).toEqual(['compile --skill "viking://agent/skills/wiki" '])
  prepareCompileHandoff(
    'alice',
    'compile --from viking://resources/a --instruction "keep sources" --skill',
  )
  expect(readCompileHandoff('bob')).toBeUndefined()
  expect(readCompileHandoff('alice')).toEqual({
    from: ['viking://resources/a'],
    instruction: 'keep sources',
  })
  clearCompileHandoff()
  expect(readCompileHandoff('alice')).toBeUndefined()
})
