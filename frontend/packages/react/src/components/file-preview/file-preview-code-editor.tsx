import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import { useEffect, useRef, useState } from 'react';
import { Compartment, EditorState, Transaction } from '@codemirror/state';
import { EditorView, keymap, lineNumbers } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { Button } from '../../shared/primitives/button/button.js';
import {
  loadPrismLanguage,
  prismLanguage,
} from '../../shared/prism-language.js';
import { prismHighlight } from './prism-highlight.js';
import type { FilePreviewLabels, FilePreviewSource } from './types.js';

export interface FilePreviewCodeEditorProps {
  draft: string;
  source: FilePreviewSource;
  saving: boolean;
  text: Required<FilePreviewLabels>;
  onChange: (value: string) => void;
}

export function FilePreviewCodeEditor(props: FilePreviewCodeEditorProps) {
  const container = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const access = useRef(new Compartment());
  const highlighting = useRef(new Compartment());
  const [highlightFailure, setHighlightFailure] = useState<{
    reason: unknown;
  }>();
  const [attempt, setAttempt] = useState(0);
  const language = prismLanguage(props.source);

  // useEffect 必要性：容器 DOM 提交后，以最新草稿创建 CodeMirror 命令式控件；
  // React 渲染无法管理该控件的 DOM、监听器与历史记录。
  // 卸载时 destroy，释放监听器并销毁插件及其高亮定时器。
  useEffect(() => {
    const view = new EditorView({
      parent: container.current!,
      state: EditorState.create({
        doc: latest.current.draft,
        extensions: [
          lineNumbers(),
          history(),
          EditorView.lineWrapping,
          keymap.of([...defaultKeymap, ...historyKeymap]),
          access.current.of([]),
          highlighting.current.of([]),
          EditorView.updateListener.of((update) => {
            if (
              update.docChanged &&
              update.state.doc.toString() !== latest.current.draft
            )
              latest.current.onChange(update.state.doc.toString());
          }),
        ],
      }),
    });
    editor.current = view;
    return () => {
      editor.current = null;
      view.destroy();
    };
  }, []);

  // useEffect 必要性：将保存状态和编辑区文案同步到 CodeMirror 的访问配置；
  // React props 无法直接更新外部控件的 Compartment，必须通过 dispatch 配置。
  // 配置同步替换，不创建独立资源；实例由创建 Effect 在卸载时销毁。
  useEffect(() => {
    const view = editor.current!;
    view.dispatch({
      effects: access.current.reconfigure([
        EditorState.readOnly.of(props.saving),
        EditorView.editable.of(!props.saving),
        EditorView.contentAttributes.of({
          'aria-label': props.text.editor,
          'aria-readonly': String(props.saving),
          tabindex: '0',
          spellcheck: 'false',
        }),
      ]),
    });
  }, [props.saving, props.text.editor]);

  // useEffect 必要性：将宿主传入的草稿同步到 CodeMirror 文档；
  // 外部控件不受 React 渲染控制，草稿也可能由宿主更新而非编辑事件产生。
  // 同步 dispatch 不持有异步资源，无需额外清理；卸载时销毁整个实例。
  useEffect(() => {
    const view = editor.current!;
    if (!view.state.toText(props.draft).eq(view.state.doc))
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: props.draft },
        annotations: Transaction.addToHistory.of(false),
      });
  }, [props.draft]);

  // useEffect 必要性：按语言和重试次数加载 Prism 语法并配置 CodeMirror 插件；
  // 异步模块加载与命令式插件安装不能在 React 渲染中执行。
  // 清理时使旧结果失效；重新配置或销毁编辑器会销毁旧插件并清除其定时器。
  useEffect(() => {
    const view = editor.current!;
    let active = true;
    setHighlightFailure(undefined);
    view.dispatch({ effects: highlighting.current.reconfigure([]) });
    void loadPrismLanguage(language)
      .then((grammar) => {
        if (active && grammar)
          view.dispatch({
            effects: highlighting.current.reconfigure(
              prismHighlight(grammar, (reason) => {
                if (active) setHighlightFailure({ reason });
              }),
            ),
          });
      })
      .catch((reason: unknown) => {
        if (active) setHighlightFailure({ reason });
      });
    return () => {
      active = false;
    };
  }, [language, attempt]);

  return (
    <>
      <div className="ov-file-preview__code-editor" ref={container} />
      {highlightFailure ? (
        <>
          <ErrorNotification
            title={props.text.highlightFailed}
            identity={highlightFailure}
            error={highlightFailure.reason}
          >
            {errorMessage(highlightFailure.reason)}
          </ErrorNotification>
          <Button
            size="small"
            title={props.text.highlightFailed}
            onClick={() => setAttempt((value) => value + 1)}
          >
            {props.text.retry}
          </Button>
        </>
      ) : null}
    </>
  );
}
