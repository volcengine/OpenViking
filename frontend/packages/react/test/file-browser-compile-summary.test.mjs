import assert from 'node:assert/strict';
import test from 'node:test';
import {
  click,
  fixture,
  mount,
  ok,
  openAction,
  target,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';

for (const [rule, english, chinese] of [
  [{ frequency: 'weekly', weekday: 1 }, 'Every Monday', '每周一'],
  [{ frequency: 'weekly', weekday: 4 }, 'Every Thursday', '每周四'],
  [{ frequency: 'weekly', weekday: 7 }, 'Every Sunday', '每周日'],
  [{ frequency: 'weekly' }, 'Every Monday', '每周一'],
  [{ frequency: 'daily' }, 'Daily', '每日'],
  [null, 'Do not update', '不更新'],
]) {
  test(`compile summary: ${english}`, async () => {
    const fetch = fixture(
      target,
      true,
      (call) =>
        call.path.endsWith('/compile/config')
          ? ok({
              to: target,
              from: ['viking://resources/source'],
              skill: 'viking://user/alice/skills/compile',
              instruction: null,
              auto_update: Boolean(rule),
              update_schedule: rule ? { ...rule, hour: 2, minute: 33 } : null,
            })
          : undefined,
      [{ to: target }],
    );
    const view = await mount(tree(fetch, target));
    const summary = (label) =>
      workflow().querySelector(`[aria-label="${label}"]`).textContent;
    const time = rule ? ' 02:33' : '';
    try {
      await openAction('Compile configuration');
      assert.equal(summary('Update schedule'), `${english}${time}`);
      const calls = fetch.calls.length;
      await view.render(tree(fetch, target, { locale: 'zh-CN' }));
      assert.equal(summary('更新时间'), `${chinese}${time}`);
      assert.equal(fetch.calls.length, calls);
      if (rule?.frequency === 'weekly') {
        await view.render(
          tree(fetch, target, {
            locale: 'zh-CN',
            labels: { workflows: { weeklySchedule: (day) => `Host ${day}` } },
          }),
        );
        assert.match(summary('更新时间'), /^Host 星期.+ 02:33$/);
        await view.render(
          tree(fetch, target, {
            locale: 'zh-CN',
            labels: { workflows: { weeklySchedule: undefined } },
          }),
        );
        assert.equal(summary('更新时间'), `${chinese}${time}`);
        assert.equal(fetch.calls.length, calls);
      }
      await click(workflow().querySelector('[aria-label="关闭弹窗"]'));
      assert.equal(workflow() === null, true);
    } finally {
      await view.unmount();
    }
  });
}
