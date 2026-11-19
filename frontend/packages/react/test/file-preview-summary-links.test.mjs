import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { withBundledPreview } from './helpers/bundled-preview.mjs';

test('summary links navigate through FileBrowser and standalone directory operations', () =>
  withBundledPreview('file-preview-summary-links', async (dom) => {
    const { document } = dom.window;
    const evidence = () => dom.window.summaryLinksEvidence;
    async function waitFor(predicate) {
      for (let attempt = 0; attempt < 400; attempt++) {
        if (predicate()) return;
        await delay(20);
      }
      assert.fail('Summary preview did not become ready');
    }
    const link = (section, label) =>
      [...document.querySelectorAll(`#${section} a`)].find(
        (node) => node.textContent === label,
      );
    await waitFor(
      () =>
        link('standalone', 'Absolute resource') &&
        link('browser', 'Relative child') &&
        link('file', 'Web reference') &&
        link('anchor-only', 'Same document'),
    );
    link('standalone', 'Absolute resource').click();
    await waitFor(() => evidence().standalone.length === 1);
    assert.deepEqual(
      [...evidence().standalone],
      ['viking://resources/guide.md'],
    );
    document.querySelectorAll('#standalone .ov-file-preview__level')[1].click();
    await waitFor(() => link('standalone', 'Relative child'));
    link('standalone', 'Relative child').click();
    await waitFor(() => evidence().standalone.length === 2);
    assert.equal(evidence().standalone[1], 'child/.overview.md');

    for (const section of ['standalone', 'browser', 'anchor-only']) {
      for (const [label, href] of [
        ['Web reference', 'https://example.com/reference'],
        ['HTTP reference', 'http://example.com/reference'],
        ['Protocol relative', '//example.com/reference'],
        ['Mail reference', 'mailto:reader@example.com'],
        ['Phone reference', 'tel:+12025550123'],
      ]) {
        const anchor = link(section, label);
        assert.equal(anchor.getAttribute('href'), href);
        assert.equal(anchor.getAttribute('target'), '_blank');
        for (const [type, button] of [
          ['auxclick', 1],
          ['contextmenu', 2],
        ]) {
          const event = new dom.window.MouseEvent(type, {
            bubbles: true,
            cancelable: true,
            button,
          });
          anchor.dispatchEvent(event);
          assert.equal(event.defaultPrevented, false);
        }
      }
    }
    assert.equal(evidence().standalone.length, 2);
    const hostLinks = [
      ['Web reference', 'https://example.com/reference'],
      ['Protocol relative', '//example.com/reference'],
      ['Mail reference', 'mailto:reader@example.com'],
      ['Phone reference', 'tel:+12025550123'],
    ];
    for (const [index, [label, target]] of hostLinks.entries()) {
      const event = new dom.window.MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        button: 0,
      });
      link('file', label).dispatchEvent(event);
      assert.equal(event.defaultPrevented, true);
      await waitFor(() => evidence().file.length === index + 1);
      assert.equal(evidence().file[index], target);
    }
    const heading = document.querySelector('#anchor-only h2[id$="details"]');
    assert.equal(heading?.textContent, 'Details');
    const scrollsBeforeAnchor = evidence().scrolled.length;
    link('anchor-only', 'Same document').click();
    await waitFor(() => evidence().scrolled.length > scrollsBeforeAnchor);
    assert.match(evidence().scrolled.at(-1), /details$/);
    const readsBeforeAnchor = evidence().reads.length;
    const browserScrolls = evidence().scrolled.length;
    link('browser', 'Same document').click();
    await waitFor(() => evidence().scrolled.length > browserScrolls);
    assert.match(evidence().scrolled.at(-1), /details$/);
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    assert.equal(link('browser', 'Unsafe')?.getAttribute('href'), null);
    link('browser', 'Unsafe').click();
    assert.equal(evidence().opened.length, 0);
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    link('browser', 'Malformed fragment').click();
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    link('browser', 'Relative child').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/docs/child'),
    );
    await waitFor(
      () =>
        document.querySelector(
          '#browser .ov-file-preview__level[aria-selected="true"]',
        )?.textContent === 'L1 Overview',
    );
    assert.equal(
      document
        .querySelector('#browser [data-file-tree-row-id][aria-selected="true"]')
        ?.getAttribute('data-file-tree-row-id'),
      'viking://resources/docs/child',
    );
    await waitFor(() => link('browser', 'Encoded resource'));
    link('browser', 'Encoded resource').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/docs/child folder'),
    );
    await waitFor(() => link('browser', 'Cross document'));
    link('browser', 'Cross document').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/docs/child folder/other'),
    );
    await waitFor(() => link('browser', 'Queried resource'));
    link('browser', 'Queried resource').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/guide.md'),
    );
    assert.equal(
      evidence().reads.some((uri) => uri.startsWith('https:')),
      false,
    );
    assert.equal(evidence().opened.length, 0);
    for (const [index, name] of ['Finance#Q4', '100%', '100%20'].entries()) {
      const section = `literal-${index}`;
      await waitFor(() => link(section, 'Literal base child'));
      const readsBeforeClick = evidence().reads.length;
      link(section, 'Literal base child').click();
      const target = `viking://resources/${name}/guide.md`;
      await waitFor(() =>
        evidence().reads.slice(readsBeforeClick).includes(target),
      );
      assert.equal(
        evidence()
          .reads.slice(readsBeforeClick)
          .includes('viking://resources/guide.md'),
        false,
      );
    }
  }));
