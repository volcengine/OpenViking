// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { languageChoice, localeLanguage, saveLanguage, savedLanguage } from '../src/i18n.mjs';

test('only a Chinese locale settles the language; a locale naming no language says nothing', () => {
  assert.equal(localeLanguage({ LANG: 'zh_CN.UTF-8' }), 'zh');
  assert.equal(localeLanguage({ LC_ALL: 'zh_TW.UTF-8', LANG: 'en_US.UTF-8' }), 'zh');
  assert.equal(localeLanguage({ LC_ALL: 'en_US.UTF-8', LANG: 'zh_CN.UTF-8' }), 'en', 'LC_ALL comes first');
  assert.equal(localeLanguage({ LC_MESSAGES: 'fr_FR.UTF-8' }), 'en');
  for (const locale of ['', 'C', 'C.UTF-8', 'POSIX']) assert.equal(localeLanguage({ LANG: locale }), null, locale);
});

test('--lang beats VIKA_LANG, which beats the saved language, which beats Chinese; the locale only settles asking', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-lang-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const env = { HOME: home, LANG: 'en_US.UTF-8' };
  // A server's en_US says little about who reads it: Chinese, but asked.
  assert.deepEqual(languageChoice({ env }), { language: 'zh', settled: false, explicit: false });
  assert.deepEqual(languageChoice({ env: { HOME: home } }), { language: 'zh', settled: false, explicit: false });
  assert.deepEqual(languageChoice({ env: { HOME: home, LANG: 'zh_CN.UTF-8' } }), { language: 'zh', settled: true, explicit: false });
  saveLanguage('zh', env);
  assert.equal(fs.readFileSync(path.join(home, '.vika', 'language'), 'utf8'), 'zh\n');
  assert.equal(savedLanguage(env), 'zh');
  assert.deepEqual(languageChoice({ env }), { language: 'zh', settled: true, explicit: false });
  assert.deepEqual(languageChoice({ env: { ...env, VIKA_LANG: 'en' } }), { language: 'en', settled: true, explicit: true });
  assert.deepEqual(languageChoice({ env: { ...env, VIKA_LANG: 'en' }, flag: 'zh' }), { language: 'zh', settled: true, explicit: true });
  assert.equal(languageChoice({ env: { ...env, VIKA_LANG: 'fr' } }).language, 'zh', 'an unknown VIKA_LANG is ignored');
});
