import type { TFunction } from 'i18next'

// CodeMirror requires its English phrases as protocol keys. Keep those keys
// in this adapter; the language resources use semantic keys.
export function editorPhrases(t: TFunction<'studio'>): Record<string, string> {
  return {
    Completions: t('codeEditor.completions'),
    'Folded lines': t('codeEditor.foldedLines'),
    'Unfolded lines': t('codeEditor.unfoldedLines'),
    to: t('codeEditor.to'),
    'folded code': t('codeEditor.foldedCode'),
    unfold: t('codeEditor.unfold'),
    'Fold line': t('codeEditor.foldLine'),
    'Unfold line': t('codeEditor.unfoldLine'),
    'Selection deleted': t('codeEditor.selectionDeleted'),
    'Control character': t('codeEditor.controlCharacter'),
    Find: t('codeEditor.find'),
    Replace: t('codeEditor.replaceInput'),
    next: t('codeEditor.next'),
    previous: t('codeEditor.previous'),
    all: t('codeEditor.selectAll'),
    'match case': t('codeEditor.matchCase'),
    regexp: t('codeEditor.regexp'),
    'by word': t('codeEditor.wholeWord'),
    replace: t('codeEditor.replace'),
    'replace all': t('codeEditor.replaceAll'),
    close: t('codeEditor.close'),
    'Go to line': t('codeEditor.goToLine'),
    go: t('codeEditor.go'),
    'current match': t('codeEditor.currentMatch'),
    'on line': t('codeEditor.onLine'),
    'replaced match on line $': t('codeEditor.replacedOnLine'),
    'replaced $ matches': t('codeEditor.replacedMatches'),
  }
}
