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

const noNewline = '\\ No newline at end of file'

it('keeps a final-newline change on the correct side', () => {
  const header = '@@ -1 +1 @@'
  expect(
    parseSnapshotDiff([header, '-hello', noNewline, '+hello'].join('\n'))[0].rows,
  ).toEqual([
    {
      kind: 'changed',
      oldLine: 1,
      newLine: 1,
      oldText: 'hello',
      newText: 'hello',
      oldNoNewline: true,
    },
  ])
  expect(
    parseSnapshotDiff([header, '-hello', '+hello', noNewline].join('\n'))[0].rows,
  ).toEqual([
    {
      kind: 'changed',
      oldLine: 1,
      newLine: 1,
      oldText: 'hello',
      newText: 'hello',
      newNoNewline: true,
    },
  ])
})

it('preserves both markers on changed lines and on shared context', () => {
  for (const diff of [
    ['@@ -1 +1 @@', '-old', noNewline, '+new', noNewline],
    ['@@ -1,2 +1,2 @@', '-old', '+new', ' shared', noNewline],
  ]) {
    const row = parseSnapshotDiff(diff.join('\n'))[0].rows.at(-1)
    expect(row?.oldNoNewline).toBe(true)
    expect(row?.newNoNewline).toBe(true)
  }
})
