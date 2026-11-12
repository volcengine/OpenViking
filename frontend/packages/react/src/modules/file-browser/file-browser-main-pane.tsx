import type { ReactNode } from 'react';

import { FileBrowserDetail } from './file-browser-detail.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import { FileBrowserFailure } from './file-browser-failure.js';
import type { FileBrowserApi } from './types.js';
import type { FilePreviewLabels } from '../../components/file-preview/index.js';
import type { Locale } from '../../shared/locale.js';

/**
 * 右栏：当前资源的预览，或空态、或读取失败的说明。
 *
 * 读失败与「没选中」是两件事：前者有选中的资源却取不到内容，必须显示原因与
 * 重试；后者才是空态插画。把失败画成空态会让一次 403 看起来像「还没选文件」。
 *
 * The right column: the preview of the current resource, the empty state, or the
 * explanation of a failed read.
 *
 * A read failure and "nothing selected" are two different things: the former has a
 * selected resource it could not load and needs the reason plus a retry, while only
 * the latter is the empty-state artwork. Painting a failure as the empty state makes a
 * 403 look like "no file selected yet".
 */
export interface FileBrowserMainPaneProps {
  browser: FileBrowserApi;
  locale: Locale;
  labels: FilePreviewLabels;
  emptyLabel: string;
  errorLabel: string;
  retryLabel: string;
  titleExtra?: ReactNode;
  extraActions?: ReactNode;
  readOnly: boolean;
}

export function FileBrowserMainPane({
  browser,
  locale,
  labels,
  emptyLabel,
  errorLabel,
  retryLabel,
  titleExtra,
  extraActions,
  readOnly,
}: FileBrowserMainPaneProps) {
  if (browser.previewTabs && browser.previewTabs.length > 0) {
    return (
      <FileBrowserDetail
        tabs={browser.previewTabs}
        activeKey={browser.previewKey}
        locale={locale}
        labels={labels}
        footer={browser.previewFooter}
        titleExtra={titleExtra}
        extraActions={extraActions}
        readOnly={readOnly}
        onActiveKeyChange={browser.actions.setPreviewKey}
      />
    );
  }

  if (browser.currentUri && browser.previewError) {
    return (
      <div className="ov-file-browser__detail">
        <FileBrowserFailure
          message={errorLabel}
          detail={browser.previewError.message}
          retryLabel={retryLabel}
          onRetry={() => void browser.actions.refresh()}
        />
      </div>
    );
  }

  return (
    <div className="ov-file-browser__detail">
      <FileBrowserEmpty label={emptyLabel} />
    </div>
  );
}
