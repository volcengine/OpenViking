import { useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { Compartment, EditorState } from '@codemirror/state'
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
} from '@codemirror/view'
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from '@codemirror/commands'
import {
  syntaxHighlighting,
  defaultHighlightStyle,
  bracketMatching,
  foldGutter,
  indentOnInput,
} from '@codemirror/language'
import type { LanguageSupport } from '@codemirror/language'
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
} from '@codemirror/autocomplete'
import { oneDarkHighlightStyle } from '@codemirror/theme-one-dark'

const languageLoaders: Partial<Record<string, () => Promise<LanguageSupport>>> =
  {
    javascript: () =>
      import('@codemirror/lang-javascript').then((m) =>
        m.javascript({ jsx: true, typescript: false }),
      ),
    typescript: () =>
      import('@codemirror/lang-javascript').then((m) =>
        m.javascript({ jsx: true, typescript: true }),
      ),
    python: () => import('@codemirror/lang-python').then((m) => m.python()),
    json: () => import('@codemirror/lang-json').then((m) => m.json()),
    html: () => import('@codemirror/lang-html').then((m) => m.html()),
    css: () => import('@codemirror/lang-css').then((m) => m.css()),
    markdown: () =>
      import('@codemirror/lang-markdown').then((m) => m.markdown()),
    rust: () => import('@codemirror/lang-rust').then((m) => m.rust()),
    cpp: () => import('@codemirror/lang-cpp').then((m) => m.cpp()),
    java: () => import('@codemirror/lang-java').then((m) => m.java()),
    sql: () => import('@codemirror/lang-sql').then((m) => m.sql()),
    xml: () => import('@codemirror/lang-xml').then((m) => m.xml()),
    yaml: () => import('@codemirror/lang-yaml').then((m) => m.yaml()),
  }

const extMap: Record<string, string> = {
  ts: 'typescript',
  tsx: 'typescript',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  py: 'python',
  pyw: 'python',
  rs: 'rust',
  c: 'cpp',
  h: 'cpp',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  hpp: 'cpp',
  java: 'java',
  json: 'json',
  html: 'html',
  htm: 'html',
  svg: 'xml',
  xml: 'xml',
  css: 'css',
  scss: 'css',
  less: 'css',
  md: 'markdown',
  markdown: 'markdown',
  sql: 'sql',
  yml: 'yaml',
  yaml: 'yaml',
}

function detectLanguage(filename: string): string | null {
  const ext = filename.toLowerCase().split('.').pop() || ''
  return extMap[ext] || null
}

export interface CodeEditorHandle {
  getContent: () => string
}

function accessExtensions(readOnly: boolean) {
  return [
    EditorState.readOnly.of(readOnly),
    EditorView.editable.of(!readOnly),
    ...(readOnly
      ? [keymap.of(searchKeymap)]
      : [
          highlightActiveLineGutter(),
          highlightActiveLine(),
          foldGutter(),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          autocompletion(),
          highlightSelectionMatches(),
          keymap.of([
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...searchKeymap,
            ...historyKeymap,
            indentWithTab,
          ]),
        ]),
  ]
}

interface CodeEditorProps {
  initialContent: string
  filename: string
  isDark?: boolean
  readOnly?: boolean
  enableLanguageSupport?: boolean
  lineWrapping?: boolean
  appearance?: 'editor' | 'plain'
  language?: string
  ariaLabel?: string
  onChange?: (content: string) => void
}

export const CodeEditor = forwardRef<CodeEditorHandle, CodeEditorProps>(
  function CodeEditor(
    {
      initialContent,
      filename,
      isDark = false,
      readOnly = false,
      enableLanguageSupport = true,
      lineWrapping = false,
      appearance = 'editor',
      language,
      ariaLabel,
      onChange,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const viewRef = useRef<EditorView | null>(null)
    const onChangeRef = useRef(onChange)
    const contentRef = useRef(initialContent)
    const readOnlyRef = useRef(readOnly)
    const syncingRef = useRef(false)
    const accessRef = useRef(new Compartment())
    onChangeRef.current = onChange
    contentRef.current = initialContent
    readOnlyRef.current = readOnly

    useImperativeHandle(ref, () => ({
      getContent: () => viewRef.current?.state.doc.toString() ?? initialContent,
    }))

    useEffect(() => {
      if (!containerRef.current) return

      let destroyed = false

      const setup = async () => {
        const extensions = [
          lineNumbers(),
          drawSelection(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          history(),
          EditorView.contentAttributes.of(
            ariaLabel ? { 'aria-label': ariaLabel } : {},
          ),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !syncingRef.current)
              onChangeRef.current?.(update.state.doc.toString())
          }),
          EditorView.theme({
            '&': {
              height: '100%',
              backgroundColor:
                appearance === 'plain'
                  ? 'var(--background)'
                  : 'color-mix(in oklch, var(--muted) 28%, var(--background))',
              color: 'var(--foreground)',
            },
            '&.cm-focused': { outline: 'none' },
            '.cm-scroller': {
              overflow: 'auto',
              backgroundColor: 'transparent',
            },
            '.cm-content': {
              caretColor: 'var(--primary)',
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              fontSize: '13px',
            },
            '.cm-gutters': {
              backgroundColor:
                appearance === 'plain'
                  ? 'var(--background)'
                  : 'color-mix(in oklch, var(--muted) 44%, var(--background))',
              borderRight:
                appearance === 'plain' ? 'none' : '1px solid var(--border)',
              color: 'var(--muted-foreground)',
              fontSize: '13px',
            },
            '.cm-lineNumbers .cm-gutterElement': {
              paddingLeft: '8px',
              paddingRight: '10px',
            },
            '.cm-cursor, .cm-dropCursor': {
              borderLeftColor: 'var(--primary)',
            },
            '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
              {
                backgroundColor:
                  'color-mix(in oklch, var(--primary) 24%, transparent)',
              },
            '.cm-activeLine, .cm-activeLineGutter': {
              backgroundColor:
                'color-mix(in oklch, var(--primary) 8%, transparent)',
            },
          }),
        ]

        if (lineWrapping) {
          extensions.push(EditorView.lineWrapping)
        }

        if (isDark) {
          extensions.push(syntaxHighlighting(oneDarkHighlightStyle))
        }

        const lang = enableLanguageSupport
          ? (language ?? detectLanguage(filename))
          : null
        if (lang && languageLoaders[lang]) {
          try {
            const langSupport = await languageLoaders[lang]()
            if (!destroyed) extensions.push(langSupport)
          } catch {
            /* fallback to no language support */
          }
        }

        if (destroyed) return

        extensions.push(
          accessRef.current.of(accessExtensions(readOnlyRef.current)),
        )
        const state = EditorState.create({
          doc: contentRef.current,
          extensions,
        })

        const view = new EditorView({
          state,
          parent: containerRef.current!,
        })

        viewRef.current = view
      }

      void setup()

      return () => {
        destroyed = true
        viewRef.current?.destroy()
        viewRef.current = null
      }
    }, [
      enableLanguageSupport,
      appearance,
      filename,
      isDark,
      lineWrapping,
      language,
      ariaLabel,
    ])

    useEffect(() => {
      const view = viewRef.current
      if (!view || view.state.doc.toString() === initialContent) return
      syncingRef.current = true
      try {
        view.dispatch({
          changes: {
            from: 0,
            to: view.state.doc.length,
            insert: initialContent,
          },
        })
      } finally {
        syncingRef.current = false
      }
    }, [initialContent])

    useEffect(() => {
      viewRef.current?.dispatch({
        effects: accessRef.current.reconfigure(accessExtensions(readOnly)),
      })
    }, [readOnly])

    return (
      <div
        ref={containerRef}
        className={
          appearance === 'plain'
            ? 'h-full min-h-0 overflow-hidden bg-background'
            : 'h-full min-h-0 overflow-hidden rounded-md border shadow-sm'
        }
      />
    )
  },
)
