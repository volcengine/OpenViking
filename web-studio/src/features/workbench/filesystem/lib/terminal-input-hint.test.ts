import { describe, expect, it } from 'vitest'

import { getCommandInputHint } from '#/features/workbench/filesystem/lib/terminal-input-hint'

const templates = [
  { insertText: '/add-skill ', usage: '/add-skill <Git URL> [-p <URI>]' },
  { insertText: '/session ', usage: '/session <子命令>' },
  { insertText: '/session switch ', usage: '/session switch <session_id>' },
  { insertText: '/status', usage: '/status' },
]

describe('command parameter hint', () => {
  it('shows arguments after a selected command without adding them to the value', () => {
    expect(getCommandInputHint('/add-skill ', templates)).toBe(
      '<Git URL> [-p <URI>]',
    )
    expect(getCommandInputHint('/add-skill', templates)).toBe(
      ' <Git URL> [-p <URI>]',
    )
  })

  it('prefers session subcommands and hides hints once arguments are entered', () => {
    expect(getCommandInputHint('/session switch ', templates)).toBe(
      '<session_id>',
    )
    expect(getCommandInputHint('/session switch my-session', templates)).toBe(
      '',
    )
  })

  it('does not show parameters for a command without arguments', () => {
    expect(getCommandInputHint('/status', templates)).toBe('')
  })
})
