import { describe, expect, it } from 'vitest'
import {
  isExperienceDirectory,
  needsExperienceDetailTab,
  sessionIdFromDirectory,
  usesWorkbenchDirectoryView,
} from './document-kind'

describe('IDE document views', () => {
  it('keeps ordinary directories on the L0/L1 preview path', () => {
    expect(usesWorkbenchDirectoryView('viking://user/alice/memories/')).toBe(
      false,
    )
    expect(usesWorkbenchDirectoryView('viking://resources/wiki/')).toBe(false)
    expect(
      usesWorkbenchDirectoryView(
        'viking://system/compile/example-skills/daily-report/',
      ),
    ).toBe(true)
    expect(usesWorkbenchDirectoryView('viking://user/alice/sessions/')).toBe(
      true,
    )
    expect(
      usesWorkbenchDirectoryView('viking://user/alice/memories/experiences/'),
    ).toBe(true)
  })
  it('recognizes canonical and peer-scoped experience directories', () => {
    for (const uri of [
      'viking://user/alice/memories/experiences/',
      'viking://user/alice/peers/project/memories/experiences',
      'viking://agent/memories/experiences/nested/',
    ])
      expect(isExperienceDirectory(uri)).toBe(true)
    expect(
      isExperienceDirectory('viking://resources/memories/experiences'),
    ).toBe(false)
    expect(
      isExperienceDirectory('viking://user/alice/memories/experiences-archive'),
    ).toBe(false)
  })
  it('uses a detail tab only for experience files outside the native board scope', () => {
    expect(
      needsExperienceDetailTab(
        'viking://user/alice/memories/experiences/review.md',
      ),
    ).toBe(false)
    expect(
      needsExperienceDetailTab(
        'viking://user/alice/peers/project/memories/experiences/review.md',
      ),
    ).toBe(true)
    expect(
      needsExperienceDetailTab(
        'viking://agent/memories/experiences/nested/review.md',
      ),
    ).toBe(true)
    expect(
      needsExperienceDetailTab(
        'viking://user/alice/memories/experiences/data.json',
      ),
    ).toBe(false)
  })
  it('resolves session directories without mistaking nested files for sessions', () => {
    expect(
      sessionIdFromDirectory('viking://user/alice/sessions/session-1/'),
    ).toBe('session-1')
    expect(sessionIdFromDirectory('viking://user/alice/sessions/')).toBeNull()
    expect(
      sessionIdFromDirectory(
        'viking://user/alice/sessions/session-1/messages.jsonl',
      ),
    ).toBeNull()
  })
})
