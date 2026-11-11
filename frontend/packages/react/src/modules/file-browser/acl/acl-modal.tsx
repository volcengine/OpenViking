import { Button } from '../../../shared/components/button/button.js';
import { useEffect, useId, useRef, type RefObject } from 'react';
import { Modal } from '../../../shared/components/modal/modal.js';
import type { Locale } from '../../../shared/locale.js';
import type { OvIdentityValue } from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclOptions,
  FileBrowserAclReport,
} from '../acl-types.js';
import { AclAddPrincipal } from './acl-add-principal.js';
import { AclConfirmation } from './acl-confirmation.js';
import { AclEntry } from './acl-entry.js';
import { levelLabel, type AclText } from './acl-labels.js';
import { useAclReport } from './use-acl-report.js';

export function AclModal({
  uri,
  open,
  text,
  locale,
  context,
  triggerRef,
  accountId,
  options,
  onClose,
  onUpdated,
}: {
  uri: string;
  open: boolean;
  text: AclText;
  locale: Locale;
  context: FileBrowserAclContext;
  triggerRef: RefObject<HTMLButtonElement | null>;
  accountId?: OvIdentityValue;
  options?: FileBrowserAclOptions;
  onClose: () => void;
  onUpdated: (report: FileBrowserAclReport) => void;
}) {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const model = useAclReport({
    uri,
    context,
    open,
    readOnly: Boolean(options?.readOnly),
    onUpdated,
  });
  const disabled = Boolean(options?.readOnly) || model.phase !== 'ready';
  const entries = model.report?.effective_entries ?? [];
  const direct = new Map(
    model.report?.direct_entries.map((entry) => [entry.principal, entry.level]),
  );
  const wasConfirming = useRef(false);
  // useEffect 必要性：确认区卸载后将浏览器焦点放回仍存在的标题，
  // DOM 节点需提交后才可聚焦；每次确认切换重新判断，无订阅需要清理。
  useEffect(() => {
    if (wasConfirming.current && !model.confirmation) heading.current?.focus();
    wasConfirming.current = Boolean(model.confirmation);
  }, [model.confirmation]);
  return (
    <Modal
      id={id}
      open={open}
      title={text.title}
      closeLabel={text.close}
      triggerRef={triggerRef}
      className="ov-file-browser-acl"
      locale={locale}
      onClose={() => {
        if (model.phase === 'saving') return;
        if (model.confirmation) model.cancel();
        else onClose();
      }}
    >
      <h2 ref={heading} tabIndex={-1} className="ov-file-browser-acl__title">
        {text.title}
      </h2>
      <p className="ov-file-browser-acl__uri">{uri}</p>
      {model.error ? (
        <div className="ov-file-browser-acl__failure" role="alert">
          <p>{model.report ? text.saveFailed : text.loadFailed}</p>
          <p>{model.error.message}</p>
          {!model.report ? (
            <Button
              size="small"
              type="default"
              htmlType="button"
              onClick={model.retry}
            >
              {text.retry}
            </Button>
          ) : null}
        </div>
      ) : null}
      <div role="status" className="ov-file-browser-acl__status">
        {model.phase === 'saving'
          ? text.saving
          : model.phase === 'loading'
            ? text.loading
            : model.saved
              ? text.saved
              : ''}
      </div>
      {model.confirmation ? (
        <AclConfirmation model={model} text={text} />
      ) : model.report ? (
        <>
          {model.report.acl_mode === 'restricted' ? (
            <div className="ov-file-browser-acl__restricted">
              <p>{text.restricted}</p>
              <Button
                size="small"
                type="default"
                htmlType="button"
                disabled={disabled}
                onClick={model.restore}
              >
                {text.restore}
              </Button>
            </div>
          ) : null}
          {options?.readOnly ? (
            <p className="ov-file-browser-acl__hint">{text.readOnly}</p>
          ) : null}
          <AclAddPrincipal
            entries={entries}
            text={text}
            disabled={disabled}
            context={context}
            accountId={accountId}
            options={options}
            onAdd={(added) => model.change([...entries, ...added])}
          />
          <ul
            className="ov-file-browser-acl__entries"
            aria-busy={model.phase === 'saving'}
          >
            <li className="ov-file-browser-acl__entry">
              <span className="ov-file-browser-acl__avatar" aria-hidden="true">
                A
              </span>
              <div className="ov-file-browser-acl__identity">
                <span className="ov-file-browser-acl__name">
                  {text.administrator}
                </span>
                <span className="ov-file-browser-acl__source">
                  {text.administratorDescription}
                </span>
              </div>
              <span className="ov-file-browser-acl__fixed">
                {levelLabel('manage', text)}
              </span>
            </li>
            {entries.map((entry) => (
              <AclEntry
                key={entry.principal}
                entry={entry}
                text={text}
                disabled={disabled}
                source={
                  direct.get(entry.principal) === entry.level
                    ? text.direct
                    : text.inherited
                }
                onChange={(level) =>
                  model.change(
                    entries.map((item) =>
                      item.principal === entry.principal
                        ? { ...item, level }
                        : item,
                    ),
                  )
                }
                onRemove={() =>
                  model.change(
                    entries.filter(
                      (item) => item.principal !== entry.principal,
                    ),
                  )
                }
              />
            ))}
          </ul>
        </>
      ) : null}
    </Modal>
  );
}
