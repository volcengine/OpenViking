import { createElement as h } from 'react';
import { OpenVikingProvider, useFileBrowser } from './internal-hooks.mjs';
import { mount, ok, recordingFetch } from './module-dom.mjs';
const root = 'viking://user/alice';
const wiki = `${root}/resources/handbook`;

export async function metadataFixture({
  uri,
  content,
  kinds,
  registry,
  taggedTarget,
  fullRead,
  attrs,
  canonicalUri,
}) {
  let browser;
  let document = content;
  const history = [];
  const canonical = canonicalUri ?? uri.replace('viking://~', root);
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/configs'))
      return registry ?? ok({ targets: [{ to: wiki }] });
    if (path.endsWith('/attrs'))
      return (
        attrs ??
        ok({
          uri: canonical,
          attrs: {
            tags: query.uri === taggedTarget ? ['compile_target=true'] : [],
          },
        })
      );
    if (path.endsWith('/stat')) return ok({ uri: canonical, isDir: false });
    if (path.endsWith('/ls')) return ok([{ uri: canonical, isDir: false }]);
    if (path.endsWith('/read')) {
      const limit = Number(query.limit);
      if (limit < 0 && fullRead) return fullRead;
      const lines = document.match(/[^\n]*\n|[^\n]+$/g) ?? [];
      // Deliberately emulate an old server that ignores raw=false.
      return ok(limit < 0 ? document : lines.slice(0, limit).join(''));
    }
    if (path.endsWith('/write')) return ok({});
    throw new Error(`Unexpected request: ${path}`);
  });
  function Probe() {
    browser = useFileBrowser({
      rootUri: root,
      currentUri: uri,
      controlled: true,
      onUriChange() {},
      dashboardKinds: kinds,
    });
    const tab = browser.previewTabs?.find((item) => item.key === uri);
    history.push(tab?.source?.content);
    return h(
      'div',
      null,
      browser.previewTabs?.find((item) => item.key === browser.previewKey)
        ?.content,
    );
  }
  const view = await mount(h(OpenVikingProvider, { fetch }, h(Probe)));
  return {
    fetch,
    view,
    history,
    setContent(value) {
      document = value;
    },
    get browser() {
      return browser;
    },
    get tab() {
      return browser.previewTabs?.find((item) => item.key === uri);
    },
  };
}
