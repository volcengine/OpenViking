import { describe, expect, it } from 'vitest'
import {
  isExperienceDirectory,
  isExperienceFile,
  sessionIdFromDirectory,
} from './document-kind'

describe('IDE document views', () => {
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
  it('uses specialized previews only for experience markdown', () => {
    expect(
      isExperienceFile('viking://user/alice/memories/experiences/review.md'),
    ).toBe(true)
    expect(
      isExperienceFile('viking://user/alice/memories/experiences/data.json'),
    ).toBe(false)
    expect(isExperienceFile('viking://resources/experiences.md')).toBe(false)
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
