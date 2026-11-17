import { useCallback, useId, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { TabList } from '../../shared/primitives/tabs/tab-list.js';
import { normalizeResourceUri } from '../../shared/resource-uri.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import { readCompileCatalog } from './dashboard-data.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { PreviewQueryState } from './preview-query-state.js';
import { PreviewCompileDetails } from './preview-compile-details.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';
export type { CompileField } from './preview-compile-details.js';

export function PreviewCompileConfig(
  props: PreviewWorkflowProps & {
    canWrite: boolean;
    loadingText: string;
    retryText: string;
  },
) {
  const query = useDashboardQuery(
    useCallback(
      async (client: OvClient, options: OvRequestOptions) => {
        const uri = normalizeResourceUri(
          await resolveDashboardUri(client, props.uri, options),
        );
        const catalog = await readCompileCatalog(client, uri, options);
        return catalog.named
          ? catalog.targets.filter(
              (item) =>
                normalizeResourceUri(item.to) === uri &&
                Boolean(item.compile_name),
            )
          : [{ to: uri }];
      },
      [props.uri],
    ),
    props.requestOptions,
  );
  const [selected, setSelected] = useState<string>();
  const tabsId = useId();
  const choices = query.data ?? [];
  const choice =
    choices.find((item) => item.compile_name === selected) ?? choices[0];
  return (
    <>
      <PreviewQueryState
        query={query}
        text={{
          ...props.text,
          loading: props.loadingText,
          retry: props.retryText,
        }}
      />
      {choices.length > 1 ? (
        <TabList
          id={tabsId}
          label={props.text.configuration}
          className="ov-compile-config__tabs"
          activeKey={choice!.compile_name!}
          items={choices.map((item) => ({
            key: item.compile_name!,
            label: item.compile_name,
          }))}
          manual
          onChange={setSelected}
        />
      ) : null}
      {query.data && !choices.length ? (
        <p role="status">{props.text.configurationEmpty}</p>
      ) : null}
      {choice ? (
        <div
          role={choices.length > 1 ? 'tabpanel' : undefined}
          aria-labelledby={
            choices.length > 1 ? `${tabsId}-${choice.compile_name}` : undefined
          }
        >
          <PreviewCompileDetails
            key={choice.compile_name ?? choice.to}
            {...props}
            compileName={choice.compile_name}
          />
        </div>
      ) : null}
    </>
  );
}
