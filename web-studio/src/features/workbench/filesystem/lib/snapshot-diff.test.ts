import { expect, it } from 'vitest'

import { parseSnapshotDiff } from '#/features/workbench/filesystem/lib/snapshot-diff'

it('aligns changed lines and keeps line numbers across hunks', () => {
  const hunks = parseSnapshotDiff(
    [
      '--- a/note.md',
      '+++ b/note.md',
      '@@ -2,2 +2,3 @@',
      ' same',
      '-old',
      '+new',
      '+extra',
      '@@ -9,1 +10,0 @@',
      '-gone',
    ].join('\n'),
  )

  expect(hunks).toHaveLength(2)
  expect(hunks[0].rows).toEqual([
    {
      kind: 'context',
      oldLine: 2,
      newLine: 2,
      oldText: 'same',
      newText: 'same',
    },
    { kind: 'changed', oldLine: 3, newLine: 3, oldText: 'old', newText: 'new' },
    { kind: 'added', newLine: 4, newText: 'extra' },
  ])
  expect(hunks[1].rows).toEqual([
    { kind: 'removed', oldLine: 9, oldText: 'gone' },
  ])
})
