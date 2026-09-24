import assert from 'node:assert/strict';
import { createLanguagePreference } from './language-preference.js';

export function testBrowser(options = {}) {
  const local = new Map(Object.entries(options.local || {}));
  const events = new EventTarget();
  const writes = [];
  let cookie = options.cookie || '';
  let location = new URL(options.url || 'https://openviking.ai/');
  const browser = {
    navigator: { languages: options.languages ?? ['en-US'], language: options.language ?? 'en-US' },
    get location() { return location; },
    document: {
      get cookie() { if (options.denied) throw new Error('blocked'); return cookie; },
      set cookie(value) {
        if (options.denied) throw new Error('blocked');
        writes.push(value);
        if (!options.cookieBlocked) cookie = value.split(';')[0];
      },
    },
    localStorage: {
      getItem(key) { if (options.denied) throw new Error('blocked'); return local.get(key) ?? null; },
      setItem(key, value) { if (options.denied) throw new Error('blocked'); local.set(key, value); },
    },
    history: { state: { navigation: 7 }, replaceState(state, _, href) { assert.equal(state, this.state); location = new URL(href, location); } },
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events),
  };
  return { browser, local, writes, setCookie(value) { cookie = value; } };
}

/** Same behavioral cases are run by each site's own test runner. */
export function languageContract(test) {
  const key = 'openviking-language-preference';
  for (const [tag, full, bilingual] of [
    ['zh-CN', 'zh', 'zh'], ['zh-SG', 'zh', 'zh'], ['zh-Hans', 'zh', 'zh'],
    ['zh-TW', 'zh-TW', 'zh'], ['zh-HK', 'zh-TW', 'zh'], ['zh-MO', 'zh-TW', 'zh'],
    ['zh-Hant', 'zh-TW', 'zh'], ['zh_Hant_TW', 'zh-TW', 'zh'], ['zh-Hans-TW', 'zh', 'zh'],
    ['ja-JP', 'ja', 'en'], ['ko-KR', 'ko', 'en'], ['es-419', 'es', 'en'],
    ['en-US', 'en', 'en'], ['fr-FR', 'en', 'en'],
  ]) {
    test(`first browser language ${tag}`, () => {
      const { browser, local, writes } = testBrowser({ languages: [tag, 'zh-CN'] });
      const policy = createLanguagePreference(browser);
      assert.equal(policy.resolve(), full);
      assert.equal(policy.resolve(undefined, true), bilingual);
      assert.equal(policy.read(), 'auto');
      assert.equal(local.size, 0);
      assert.deepEqual(writes, []);
    });
  }
  test('uses navigator.language only when the first language is missing', () => {
    const { browser } = testBrowser({ languages: [], language: 'zh-SG' });
    assert.equal(createLanguagePreference(browser).resolve(), 'zh');
    assert.equal(createLanguagePreference(undefined).resolve(), 'en');
  });
  test('URL overrides cookie and local preference without persisting', () => {
    const fixture = testBrowser({ url: 'https://blog.openviking.ai/post/a/?lang=en', cookie: `${key}=zh`, local: { [key]: 'ja' } });
    const policy = createLanguagePreference(fixture.browser);
    assert.equal(policy.resolve(), 'en');
    assert.equal(policy.read(), 'zh');
    assert.equal(fixture.local.get(key), 'ja');
    assert.deepEqual(fixture.writes, []);
  });
  test('ignores invalid query values and supports Studio lng alias', () => {
    const { browser } = testBrowser({ url: 'https://openviking.net/studio/?lang=invalid&lng=zh-SG', cookie: `${key}=ja` });
    const policy = createLanguagePreference(browser);
    assert.equal(policy.resolve(), 'ja');
    assert.equal(policy.resolve(policy.query(['lang', 'lng']), true), 'zh');
    assert.equal(policy.parse('auto'), undefined);
    assert.equal(policy.parse('zh<script>'), undefined);
  });
  test('auto cookie overrides stale local manual preference', () => {
    const fixture = testBrowser({ cookie: `${key}=auto`, local: { [key]: 'zh' } });
    assert.equal(createLanguagePreference(fixture.browser).resolve(), 'en');
  });
  test('ignores malformed cookies and old language records', () => {
    const fixture = testBrowser({ languages: ['zh-HK'], cookie: `${key}=%ZZ; openviking-preferences=%7B%22lang%22%3A%22en%22%7D`, local: { i18nextLng: 'en', 'blog.lang': 'en' } });
    assert.equal(createLanguagePreference(fixture.browser).resolve(), 'zh-TW');
  });
  test('explicitly saves a click on the current automatic language', () => {
    const fixture = testBrowser({ languages: ['zh-CN'] });
    const policy = createLanguagePreference(fixture.browser);
    assert.equal(policy.resolve(), 'zh');
    policy.save('zh');
    assert.equal(policy.read(), 'zh');
    assert.equal(createLanguagePreference(fixture.browser).read(), 'zh');
    assert.equal(fixture.local.get(key), 'zh');
  });
  test('keeps the original choice when a bilingual site falls back', () => {
    const fixture = testBrowser();
    const policy = createLanguagePreference(fixture.browser);
    policy.save('ja');
    assert.equal(policy.resolve(undefined, true), 'en');
    assert.equal(policy.read(), 'ja');
    assert.equal(fixture.local.get(key), 'ja');
  });
  test('restores auto and clears only language query parameters', () => {
    const fixture = testBrowser({ url: 'https://openviking.net/studio/?lang=en&lng=en&tab=1#hello', languages: ['zh-SG'] });
    const policy = createLanguagePreference(fixture.browser);
    policy.clearQuery(['lang', 'lng']);
    policy.save('auto');
    assert.equal(policy.read(), 'auto');
    assert.equal(policy.resolve(), 'zh');
    assert.equal(fixture.browser.location.href, 'https://openviking.net/studio/?tab=1#hello');
    assert.equal(fixture.local.get(key), 'auto');
  });
  for (const host of ['openviking.ai', 'blog.openviking.ai', 'docs.openviking.net', 'openviking.net']) {
    test(`writes scoped secure cookie for ${host}`, () => {
      const fixture = testBrowser({ url: `https://${host}/` });
      createLanguagePreference(fixture.browser).save('es');
      const cookie = fixture.writes[0];
      assert.ok(cookie.includes(`Domain=.openviking.${host.endsWith('.ai') ? 'ai' : 'net'}`));
      for (const attribute of ['Path=/', 'Max-Age=31536000', 'SameSite=Lax', 'Secure']) assert.ok(cookie.includes(attribute));
    });
  }
  test('localhost and lookalike hosts do not get a parent-domain cookie', () => {
    for (const host of ['localhost', '127.0.0.1', 'notopenviking.ai', 'openviking.net.example.com']) {
      const fixture = testBrowser({ url: `http://${host}/` });
      createLanguagePreference(fixture.browser).save('en');
      assert.ok(!fixture.writes[0].includes('Domain='));
      assert.ok(!fixture.writes[0].includes('Secure'));
    }
  });
  test('disabled storage still permits manual and automatic in-page switching', () => {
    const fixture = testBrowser({ denied: true, languages: ['zh-TW'] });
    const policy = createLanguagePreference(fixture.browser);
    policy.save('ja');
    assert.equal(policy.resolve(), 'ja');
    policy.save('auto');
    assert.equal(policy.resolve(), 'zh-TW');
  });
  test('blocked cookie writes retain local choices and observe later local changes', () => {
    const fixture = testBrowser({ cookieBlocked: true });
    const policy = createLanguagePreference(fixture.browser);
    policy.save('zh');
    assert.equal(policy.read(), 'zh');
    fixture.local.set(key, 'es');
    assert.equal(policy.read(), 'es');
  });
  test('focus and history restoration refresh preferences without overriding an explicit URL', () => {
    const fixture = testBrowser({ url: 'https://openviking.ai/?lang=en' });
    const policy = createLanguagePreference(fixture.browser);
    const observed = [];
    const unsubscribe = policy.subscribe(() => observed.push([policy.read(), policy.resolve()]));
    fixture.setCookie(`${key}=zh`);
    for (const event of ['focus', 'pageshow', 'popstate', 'storage', 'languagechange']) fixture.browser.dispatchEvent(new Event(event));
    assert.deepEqual(observed, Array(5).fill(['zh', 'en']));
    unsubscribe();
    fixture.browser.dispatchEvent(new Event('focus'));
    assert.equal(observed.length, 5);
    assert.deepEqual(fixture.writes, []);
  });
}
