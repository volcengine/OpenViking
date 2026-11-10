// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// The vika command speaks Chinese or English, one language for the whole
// run; `t` picks between the two texts, written side by side where they are
// used. The language is --lang or VIKA_LANG when given, else the one setup
// saved in ~/.vika/language, else Chinese. A Chinese locale leaves nothing
// to ask; any other (servers often say en_US or C whatever their user
// reads) has setup ask once on a terminal, Chinese first.

export const LANGUAGES = Object.freeze({ zh: '中文', en: 'English' });

let current = 'zh';

export function setLanguage(language) {
  current = language === 'en' ? 'en' : 'zh';
}

export function language() {
  return current;
}

export function t(zh, en) {
  return current === 'en' ? en : zh;
}

// Items of a list in a sentence.
export function list(items) {
  return items.join(t('、', ', '));
}

export function isLanguage(value) {
  return typeof value === 'string' && Object.hasOwn(LANGUAGES, value);
}

// zh or en from the first of LC_ALL, LC_MESSAGES and LANG that is set, or
// null when the locale names no language (C, POSIX, unset).
export function localeLanguage(env = process.env) {
  const locale = env.LC_ALL || env.LC_MESSAGES || env.LANG || '';
  if (/^zh/i.test(locale)) return 'zh';
  if (!locale || /^(C|POSIX)([._@]|$)/i.test(locale)) return null;
  return 'en';
}

function preferenceFile(env) {
  return path.join(env.HOME || os.homedir(), '.vika', 'language');
}

export function savedLanguage(env = process.env) {
  try {
    const value = fs.readFileSync(preferenceFile(env), 'utf8').trim();
    return isLanguage(value) ? value : null;
  } catch {
    return null;
  }
}

// A preference, not configuration: failing to keep it costs a question.
export function saveLanguage(value, env = process.env) {
  try {
    const file = preferenceFile(env);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(file, `${value}\n`);
  } catch {}
}

// { language, settled, explicit }: settled when there is nothing to ask,
// explicit when --lang or VIKA_LANG chose it.
export function languageChoice({ env = process.env, flag = null } = {}) {
  const explicit = [flag, env.VIKA_LANG].find(isLanguage);
  if (explicit) return { language: explicit, settled: true, explicit: true };
  const saved = savedLanguage(env);
  if (saved) return { language: saved, settled: true, explicit: false };
  return { language: 'zh', settled: localeLanguage(env) === 'zh', explicit: false };
}
