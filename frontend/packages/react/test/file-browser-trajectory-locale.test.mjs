import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

test('trajectory details translate roles without reloading messages on locale changes', async () => {
  const directory = 'viking://user/alice/memories/experiences';
  const uri = `${directory}/check.md`;
  const fetch = recordingFetch(({ path, uri: target }) => {
    if (path.endsWith('/ls')) return ok([{ uri, name: 'Experience' }]);
    if (path.endsWith('/stat'))
      return ok({ uri: target, isDir: target === directory });
    if (path.endsWith('/attrs')) return ok({ attrs: { tags: [] } });
    if (path.endsWith('/outcomes')) return ok({ outcome_distribution: [] });
    if (path.endsWith('/trajectories'))
      return ok({ items: [], total: 0, has_more: false });
    return ok('Document');
  });
  let messageReads = 0;
  const experienceDataSource = {
    async sourceGroups() {
      return [
        { id: 'latest', updatedAt: '2026-11-17', trajectories: [{ uri }] },
      ];
    },
    async trajectoryMessages() {
      messageReads += 1;
      return {
        items: ['user', 'assistant', 'tool', 'custom', ''].map((role) => ({
          role,
          content: 'Original content',
        })),
      };
    },
  };
  const render = (locale) =>
    h(
      OpenVikingProvider,
      { fetch },
      h(FileBrowser, {
        rootUri: directory,
        uri,
        locale,
        dashboardKinds: ['experience'],
        experienceDataSource,
      }),
    );
  const view = await mount(render('en-US'));
  const click = async (selector, label) => {
    const element = [...view.container.querySelectorAll(selector)].find(
      (item) => item.textContent === label,
    );
    assert.ok(element);
    await act(async () => element.click());
    await settle();
  };
  const roles = () =>
    [
      ...document.querySelectorAll('.ov-experience-trajectory-detail__role'),
    ].map((element) => element.textContent);
  try {
    await click('[role="tab"]', 'Experience dashboard');
    await click('[role="tab"]', 'Source trace');
    await click('button', 'Trajectory details');
    assert.deepEqual(roles(), ['User', 'Agent', 'Tool', 'custom', 'Unknown']);
    await view.render(render('zh-CN'));
    assert.deepEqual(roles(), ['用户', '助手', '工具', 'custom', '未知']);
    assert.ok(document.querySelector('section[aria-label="轨迹正文"]'));
    assert.ok(document.querySelector('section[aria-label="来源消息"]'));
    assert.match(
      document.querySelector('[role="dialog"]').textContent,
      /Original content/,
    );
    await view.render(render('en-US'));
    assert.deepEqual(roles(), ['User', 'Agent', 'Tool', 'custom', 'Unknown']);
    assert.equal(messageReads, 1);
  } finally {
    await view.unmount();
  }
});
