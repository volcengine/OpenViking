import { Select, SelectOption } from '../../shared/primitives/select/index.js';
import type { ReactNode } from 'react';
import { useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { PreviewQueryState } from './preview-query-state.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { readHistoryFiles } from './preview-history-files.js';
import { PreviewHistoryFile } from './preview-history-file.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';

export type PreviewHistoryProps = PreviewWorkflowProps & {
  canSaveSnapshot: boolean;
  canWrite: boolean;
  versioned: boolean;
  showContent: boolean;
  directory: boolean;
  loadingText: string;
  retryText: string;
  directoryFileSelector?: ReactNode;
  directoryHeader?: ReactNode;
};
export function PreviewHistory(props: PreviewHistoryProps) {
  const files = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readHistoryFiles(client, props.uri, options),
      [props.uri],
    ),
    props.requestOptions,
    props.directory,
  );
  const [selectedFile, setSelectedFile] = useState<string>();
  const file = props.directory
    ? (files.data?.find((uri) => uri === selectedFile) ?? files.data?.[0])
    : props.uri;
  const text = {
    ...props.text,
    loading: props.loadingText,
    retry: props.retryText,
  };
  return (
    <>
      {props.directory && !file ? (
        <div className="ov-file-history__body ov-file-history__single-pane">
          <PreviewQueryState query={files} text={text} />
          {files.data?.length === 0 ? (
            <p className="ov-file-history__state">{text.historyEmpty}</p>
          ) : null}
        </div>
      ) : null}
      {file ? (
        <PreviewHistoryFile
          directoryHeader={
            props.directory ? (
              <p className="ov-file-history__directory-path" title={props.uri}>
                {props.uri}
              </p>
            ) : undefined
          }
          directoryFileSelector={
            props.directory ? (
              <div className="ov-file-history__file-navigation">
                <Select<string>
                  name="history-file"
                  aria-label={text.selectFile}
                  value={file ? [file] : []}
                  locale={props.locale}
                  disabled={!files.data?.length}
                  onChange={(values) => setSelectedFile(values[0])}
                >
                  {files.data?.map((uri) => (
                    <SelectOption key={uri} value={uri}>
                      {uri}
                    </SelectOption>
                  ))}
                </Select>
              </div>
            ) : undefined
          }
          key={file}
          {...props}
          uri={file}
          showContent={props.directory || props.showContent}
          versioned={props.directory || props.versioned}
        />
      ) : null}
    </>
  );
}
