import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePath, parseBrowserLocation, buildPath } from './routes.js';

test('only the home path and explicit index file resolve to the index', () => {
  for (const path of ['/', '/index.html']) assert.equal(parsePath(path).route.name, 'index');
  for (const path of ['/missing', '/missing/nested/', '/post', '/post/', '/404.html', '/post/known/extra', '//post/known']) {
    const { route } = parsePath(path);
    assert.deepEqual(route, { name: 'notFound', path });
    assert.equal(buildPath(route, { lang: 'zh' }), `${path}?lang=zh`);
  }
});

test('post routing accepts one slug and its generated index document', () => {
  for (const path of ['/post/essay', '/post/essay/', '/post/essay/index.html']) {
    assert.deepEqual(parsePath(path).route, { name: 'post', slug: 'essay' });
  }
});

test('legacy hash routes preserve missing paths and query overrides', () => {
  const missing = parseBrowserLocation({ pathname: '/', search: '', hash: '#/missing/deep?lang=zh' });
  assert.equal(missing.route.name, 'notFound');
  assert.equal(buildPath(missing.route, missing.query), '/missing/deep?lang=zh');
  assert.deepEqual(parseBrowserLocation({ hash: '#/post/essay?lang=en' }).route, { name: 'post', slug: 'essay' });
});
