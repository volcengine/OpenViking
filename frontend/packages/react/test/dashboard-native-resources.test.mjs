import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h, act } from 'react';
import {
  OpenVikingProvider,
  ExperienceDashboardPane,
  WikiDashboardPane,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

for (const mode of ['experiences', 'sources', 'wiki']) {
  test(`native ${mode} resolves home aliases before filtering and isolates other users`, async () => {
    const experience = mode !== 'wiki';
    const path = experience ? '/memories/experiences' : '/resources/wiki';
    const suffix = mode === 'sources' ? '/check.md' : '';
    const alias = `viking://~${path}${suffix}`;
    const canonical = `viking://user/alice${path}${suffix}`;
    const trajectory = 'viking://user/alice/memories/trajectories/task.md';
    let malformed = false;
    const fetch = recordingFetch(({ path: endpoint, uri, body, init }) => {
      if (endpoint.endsWith('/stat'))
        return ok({ uri: canonical, isDir: mode !== 'sources' });
      if (endpoint.endsWith('/attrs'))
        return ok({
          attrs: {
            memory: {
              links: [
                { link_type: 'derived_from', to_uri: trajectory },
                {
                  link_type: 'derived_from',
                  to_uri: trajectory.replace('/alice/', '/bob/'),
                },
              ],
            },
          },
        });
      if (endpoint.endsWith('/outcomes'))
        return ok({ outcome_distribution: [] });
      if (endpoint.endsWith('/trajectories'))
        return ok({ items: [], total: 0, has_more: false });
      if (endpoint.endsWith('/search/find')) {
        assert.equal(init.method, 'POST');
        assert.deepEqual(body, {
          query: '',
          target_uri: canonical,
          tags: ['has_conflict=true'],
          level: 2,
          limit: 100,
        });
        if (malformed) return ok({ resources: 'invalid' });
        return ok({
          resources: [
            { uri: `${canonical}/check.md`, level: 2 },
            { uri: `${canonical}-other/outside.md`, level: 2 },
            { uri: `${canonical}/overview.md`, level: 1 },
          ],
          memories: [
            { uri: `${canonical}/nested/memory.md`, level: 2 },
            {
              uri: `${canonical.replace('/alice/', '/bob/')}/private.md`,
              level: 2,
            },
          ],
          skills: [{ uri: `${canonical}/about.md`, level: 2 }],
        });
      }
      if (endpoint.endsWith('/ls')) {
        assert.equal(uri, canonical);
        return ok([
          { uri: `${canonical}/check.md`, name: 'Alice check' },
          {
            uri: `${canonical.replace('/alice/', '/bob/')}/private.md`,
            name: 'Bob private',
          },
          { uri: `${canonical}-other/outside.md`, name: 'Outside' },
        ]);
      }
      throw new Error(`Unexpected endpoint ${endpoint}`);
    });
    const render = (refreshGeneration) =>
      h(
        OpenVikingProvider,
        { fetch },
        experience
          ? h(ExperienceDashboardPane, { uri: alias, refreshGeneration })
          : h(WikiDashboardPane, {
              uri: alias,
              kind: 'directory',
              refreshGeneration,
            }),
      );
    const view = await mount(render(0));
    try {
      if (mode === 'sources')
        await act(async () =>
          [...view.container.querySelectorAll('[role="tab"]')]
            .find((el) => el.textContent === 'Source trace')
            .click(),
        );
      assert.match(
        view.container.textContent,
        mode === 'sources' ? /task.md/ : /check.md|Alice check/,
      );
      assert.doesNotMatch(
        view.container.textContent,
        /bob|Bob private|Outside|outside.md/,
      );
      if (mode === 'wiki') {
        assert.equal(view.container.querySelector('dd').textContent, '3');
        assert.deepEqual(
          Array.from(
            view.container.querySelectorAll('.ov-wiki-dashboard__file-uri'),
            (item) => item.textContent,
          ),
          [
            `${canonical}/about.md`,
            `${canonical}/check.md`,
            `${canonical}/nested/memory.md`,
          ],
        );
      }
      const endpoint =
        mode === 'sources'
          ? '/attrs'
          : mode === 'wiki'
            ? '/search/find'
            : '/ls';
      const count = () =>
        fetch.calls.filter((call) => call.path.endsWith(endpoint)).length;
      const before = count();
      await view.render(render(1));
      assert.equal(count(), before + 1);
      if (mode === 'wiki') {
        malformed = true;
        await view.render(render(2));
        assert.equal(view.container.querySelector('dd').textContent, '—');
        assert.match(
          view.container.textContent,
          /Invalid Wiki conflict search response/,
        );
      }
      if (mode === 'sources')
        assert.equal(
          view.container.querySelector('[role="tab"][aria-selected="true"]')
            .textContent,
          'Source trace',
        );
    } finally {
      await view.unmount();
    }
  });
}
