import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { LibraryIcon, SpaceIcon, UserIcon } from '@openviking/react/icons';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';

const palette = JSON.parse(
  readFileSync(
    new URL('../../icons/library-swatches.json', import.meta.url),
    'utf8',
  ),
);
const themeCss = readFileSync(
  new URL('../src/theme.css', import.meta.url),
  'utf8',
);

/** A controller stand-in: the view only reads snapshots and subscribes. */
const level = (options, selected) => {
  const snapshot = {
    options,
    selected,
    enabled: true,
    loading: false,
    error: null,
    scopeKey: '[]',
    parentIds: [],
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: () => () => {},
    select() {},
    refresh() {},
  };
};
const renderCascade = (levels) =>
  renderToStaticMarkup(
    createElement(ScopeCascade, { controller: { levels }, locale: 'en-US' }),
  );

test('the level icons are public and render without a host theme', () => {
  // The library icon is a gradient swatch picked by name, not an SVG glyph.
  const library = renderToStaticMarkup(
    createElement(LibraryIcon, { name: 'a' }),
  );
  assert.match(library, /border-radius:50%/);
  // Colours come from the theme baseline first, with a literal as the last
  // resort, so a host can re-swatch the icon without editing the component.
  assert.match(library, /var\(--ov-ui-color-library-swatch-\d,/);
  assert.match(library, /var\(--ov-ui-library-swatch-\d-default,/);
  assert.match(library, /linear-gradient/);
  assert.equal(
    library,
    renderToStaticMarkup(createElement(LibraryIcon, { name: 'a' })),
  );
  // The other two are inline SVG painted with currentColor.
  for (const Icon of [SpaceIcon, UserIcon]) {
    const html = renderToStaticMarkup(createElement(Icon));
    assert.match(html, /<svg/);
    assert.match(html, /currentColor/);
    assert.match(html, /aria-hidden="true"/);
  }
});

test('the JS fallbacks and the CSS defaults share one palette', () => {
  // A single source of truth: the component imports the palette JSON and
  // theme.css is generated from it. If either side drifts, a host that loads the
  // stylesheet and one that does not would paint different colours.
  for (const [index, gradient] of palette.swatches.entries()) {
    const css = themeCss.match(
      new RegExp(`--ov-ui-library-swatch-${index + 1}-default:\\s*([^;]+);`),
    );
    assert.ok(css, `theme.css is missing swatch ${index + 1}`);
    // Compare with whitespace normalised: the CSS side is wrapped across lines by
    // Prettier, and `linear-gradient( 145deg, … )` is the same value as the
    // single-line form the palette stores.
    const normalise = (value) =>
      value
        .replaceAll(/\s+/g, ' ')
        .replaceAll(/\s*([(),])\s*/g, '$1')
        .trim();
    assert.equal(
      normalise(css[1]),
      normalise(gradient),
      `swatch ${index + 1} differs between the palette and theme.css`,
    );
  }
});

test('every library swatch is reachable and numbered from one', () => {
  // Different names must be able to select different swatches; the index feeds a
  // variable name, so it has to stay within the five declared in theme.css.
  const seen = new Set();
  for (const name of ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']) {
    const html = renderToStaticMarkup(createElement(LibraryIcon, { name }));
    const swatch = html.match(/--ov-ui-color-library-swatch-(\d),/)?.[1];
    assert.ok(swatch, `no swatch variable for ${name}`);
    assert.ok(['1', '2', '3', '4', '5'].includes(swatch));
    seen.add(swatch);
  }
  assert.ok(seen.size > 1, 'different names should reach different swatches');
});

test('the cascade fills a default icon per level when adapters return none', () => {
  // Panels are closed until a trigger is clicked, so the static markup carries
  // the three trigger icons. The chevron is painted in currentColor too, so the
  // outline glyphs are counted by their stroke.
  const html = renderCascade([
    level([{ id: 'a', label: 'Alpha' }], { id: 'a', label: 'Alpha' }),
    level([{ id: 's', label: 'Space' }], { id: 's', label: 'Space' }),
    level([{ id: 'u', label: 'User' }], { id: 'u', label: 'User' }),
  ]);
  // Library: the gradient swatch.
  assert.equal((html.match(/linear-gradient/g) ?? []).length, 1);
  // Space and user: one stroked glyph each.
  assert.equal((html.match(/stroke="currentColor"/g) ?? []).length, 2);
});

test('a host-supplied icon wins over the built-in default', () => {
  const hostIcon = createElement('em', null, 'HOST');
  const html = renderCascade([
    level([{ id: 'a', label: 'Alpha', icon: hostIcon }], {
      id: 'a',
      label: 'Alpha',
      icon: hostIcon,
    }),
    level([{ id: 's', label: 'Space', icon: hostIcon }], {
      id: 's',
      label: 'Space',
      icon: hostIcon,
    }),
    level([{ id: 'u', label: 'User', icon: hostIcon }], {
      id: 'u',
      label: 'User',
      icon: hostIcon,
    }),
  ]);
  assert.equal((html.match(/HOST/g) ?? []).length, 3);
  assert.doesNotMatch(html, /linear-gradient|stroke="currentColor"/);
});
