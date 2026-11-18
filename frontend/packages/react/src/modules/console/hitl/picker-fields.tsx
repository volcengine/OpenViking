import { useRef, useState } from 'react';
import type { ContextPickerEntry } from '../../../components/context-picker/index.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import type { VikingBotInteractionSingleRequest } from '../../../components/viking-bot/interaction/types.js';
import { ChoiceInput } from '../../../components/viking-bot/interaction/choice-input.js';
import { resolveInteractionLabels } from '../../../components/viking-bot/interaction/labels.js';
import { ChevronRightIcon, FolderIcon } from '../../../icons/index.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { useLocale } from '../../../shared/locale-context.js';
import { nameFromUri } from '../../../shared/directory-tree/rows.js';
import { CompilePicker, type PickerScope } from './picker.js';
import { hitlLabels } from './locales.js';
import { useSuggestedSources } from './directory-validation.js';
import { parseCompileUri, parseSourceUris } from './uris.js';

export type CompilePickerScope = PickerScope & {
  onOpenUri?: (uri: string) => void;
};

function entries(
  value: string,
  mode: 'source' | 'skill',
): ContextPickerEntry[] {
  const skill = mode === 'skill' ? parseCompileUri(value) : undefined;
  const uris =
    mode === 'source' ? (parseSourceUris(value) ?? []) : skill ? [skill] : [];
  return uris.map((uri) => ({
    name: nameFromUri(uri),
    uri,
    type: mode === 'skill' && uri.endsWith('/SKILL.md') ? 'file' : 'directory',
  }));
}

export function SourceField({
  field,
  scope,
  suggested,
}: {
  field: VikingBotInteractionFieldContext<string>;
  scope: CompilePickerScope;
  suggested: readonly string[];
}) {
  const labels = hitlLabels(useLocale());
  const selected = entries(field.value, 'source');
  const markEdited = useSuggestedSources(
    field,
    suggested,
    scope.client,
    scope.requestOptions,
  );
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <div className="ov-viking-bot__compile-source">
      {selected.length ? (
        <div className="ov-viking-bot__compile-source-tags">
          {selected.map(({ uri }) => (
            <span className="ov-viking-bot__compile-source-tag" key={uri}>
              {scope.onOpenUri ? (
                <Button
                  type="text"
                  title={uri}
                  disabled={field.disabled}
                  onClick={() => scope.onOpenUri?.(uri)}
                >
                  {uri}
                </Button>
              ) : (
                <span title={uri}>{uri}</span>
              )}
              <Button
                type="text"
                disabled={field.disabled}
                aria-label={labels.removeDirectory(uri)}
                onClick={() =>
                  field.onChange(
                    selected
                      .filter((entry) => entry.uri !== uri)
                      .map((entry) => entry.uri)
                      .join('\n'),
                  )
                }
              >
                ×
              </Button>
            </span>
          ))}
        </div>
      ) : null}
      <Button
        {...field.controlProps}
        ref={trigger}
        type="outline"
        className="ov-viking-bot__directory-trigger ov-viking-bot__compile-source-trigger"
        aria-haspopup="dialog"
        aria-expanded={open && !field.disabled}
        disabled={field.disabled}
        onClick={() => {
          markEdited();
          setOpen(true);
        }}
      >
        <FolderIcon width={14} height={14} />
        <span>{labels.directory}</span>
        <ChevronRightIcon width={12} height={12} />
      </Button>
      {open && !field.disabled ? (
        <CompilePicker
          mode="source"
          initial={selected}
          scope={scope}
          triggerRef={trigger}
          onOpenUri={scope.onOpenUri}
          onCancel={() => setOpen(false)}
          onConfirm={(items) => {
            field.onChange(items.map((entry) => entry.uri).join('\n'));
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

type Choice = Extract<VikingBotInteractionSingleRequest, { kind: 'choice' }>;

export function SkillField({
  field,
  request,
  scope,
}: {
  field: VikingBotInteractionFieldContext<string>;
  request: Choice;
  scope: CompilePickerScope;
}) {
  const locale = useLocale();
  const labels = hitlLabels(locale);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const chosen =
    field.value && !request.options.some((item) => item.value === field.value);
  return (
    <div className="ov-viking-bot__compile-skill">
      <Button
        {...field.controlProps}
        ref={trigger}
        type="outline"
        className="ov-viking-bot__directory-trigger ov-viking-bot__compile-source-trigger"
        aria-haspopup="dialog"
        aria-expanded={open && !field.disabled}
        disabled={field.disabled}
        onClick={() => setOpen(true)}
      >
        <FolderIcon width={14} height={14} />
        <span title={chosen ? field.value : undefined}>
          {chosen ? field.value : labels.chooseSkill}
        </span>
        <ChevronRightIcon width={12} height={12} />
      </Button>
      {request.options.length ? (
        <ChoiceInput
          request={{ ...request, allowCustomInput: false }}
          value={field.value}
          labels={resolveInteractionLabels(locale)}
          locale={locale}
          disabled={field.disabled}
          invalid={Boolean(field.error)}
          errorId={field.controlProps['aria-describedby'] ?? ''}
          onChange={field.onChange}
        />
      ) : null}
      {open && !field.disabled ? (
        <CompilePicker
          mode="skill"
          initial={entries(field.value, 'skill')}
          scope={scope}
          triggerRef={trigger}
          onOpenUri={scope.onOpenUri}
          onCancel={() => setOpen(false)}
          onConfirm={(items) => {
            if (items[0]) field.onChange(items[0].uri);
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}
