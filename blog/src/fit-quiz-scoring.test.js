import test from 'node:test';
import assert from 'node:assert/strict';
import { QUESTIONS, QUESTION_IDS, PERSONA_ORDER } from './posts/should-you-use-openviking/quiz-data.js';
import {
  evaluate, encode, decode, profile, fitScore, routeForms, whatIfs, LOW_FIT,
} from './posts/should-you-use-openviking/scoring.js';
import { promptWithAnswers, promptInterview, FACTS } from './posts/should-you-use-openviking/prompts.js';

const answersFrom = (s) => Object.fromEntries(s.split(' ').map((k, i) => [QUESTION_IDS[i], k]));

const CASES = {
  claudeArchaeologist: 'pair groundhog bigmd many grep fewtools single who cn ok ark coffee',
  hobbyist: 'pair essay homemade repo rag me single who cn love local free',
  hobbyistOverseas: 'pair essay homemade repo rag me single who overseas love local free',
  saas: 'build essay builtin many rag customers chaos cloud cn nope apikeys budget',
  bank: 'fleet essay builtin ocean spaghetti team orchestrated offline cn platform gateway procure',
  teamLead: 'fleet groundhog builtin many grep team copy cloud both ok ark budget',
  casual: 'chat never nothing prompt paste me single who cn nope apikeys free',
};

const ROUTING_IDS = ['stage', 'share', 'compliance', 'region', 'ops', 'models', 'budget'];
const FIT_ONLY_IDS = ['region', 'ops', 'models', 'budget'];

function* combos(ids, base) {
  if (!ids.length) { yield { ...base }; return; }
  const [id, ...rest] = ids;
  for (const o of QUESTIONS.find(q => q.id === id).options) yield* combos(rest, { ...base, [id]: o.key });
}

test('share codes round-trip and reject malformed input', () => {
  for (const s of Object.values(CASES)) {
    const a = answersFrom(s);
    assert.deepEqual(decode(encode(a)), a);
  }
  assert.equal(decode(''), null);
  assert.equal(decode('2aaaaaaaaaaaa'), null);
  assert.equal(decode('1aaaaaaaaaaa'), null);
  assert.equal(decode('1aaaaaaaadaaa'), null, 'region has only three options');
  assert.equal(decode('1aaaaaa-aaaaa'), null);
  assert.deepEqual(Object.keys(decode('1aa----------', { allowPartial: true })), ['stage', 'reexplain']);
});

test('worked examples land where the post says they do', () => {
  const r = (k) => evaluate(answersFrom(CASES[k]));
  assert.equal(r('claudeArchaeologist').persona, 'archaeologist');
  assert.equal(r('claudeArchaeologist').forms.top, 'personal');
  assert.equal(r('hobbyist').forms.top, 'oss');
  assert.equal(r('hobbyistOverseas').forms.top, 'oss');
  assert.equal(r('saas').persona, 'landlord');
  assert.equal(r('saas').forms.top, 'enterprise');
  assert.equal(r('bank').forms.top, 'private');
  assert.equal(r('bank').persona, 'regular');
  assert.equal(r('teamLead').forms.top, 'enterprise');
  assert.equal(r('casual').lowFit, true);
  assert.equal(r('casual').persona, 'traveler');
});

test('hard rules hold for every routing combination', () => {
  const base = answersFrom(CASES.claudeArchaeologist);
  for (const a of combos(ROUTING_IDS, base)) {
    const f = routeForms(a);
    assert.ok(f.blocks[f.top].length === 0, `top pick must be available: ${encode(a)}`);
    for (const k of Object.keys(f.scores)) assert.ok(f.scores[k] >= 0 && f.scores[k] <= 100);
    const hostedOff = a.compliance === 'account' || a.compliance === 'offline' || a.models === 'gateway';
    if (hostedOff) {
      assert.ok(f.blocks.personal.length && f.blocks.enterprise.length, `hosted must be blocked: ${encode(a)}`);
      assert.ok(!['personal', 'enterprise'].includes(f.top));
    }
    if (a.share === 'team' || a.share === 'customers' || a.stage === 'fleet') assert.notEqual(f.top, 'personal');
    if (f.tie) assert.ok(f.tie.between.includes(f.top));
    if (a.region === 'overseas' && !hostedOff) {
      // Overseas readers can still use the Beijing-hosted service; they get a latency note, not a block.
      assert.equal(f.blocks.enterprise.length, 0);
      assert.ok(f.notes.enterprise.includes('regionOverseas'));
    }
  }
});

test('region, ops, models and budget never move the fit score', () => {
  for (const s of Object.values(CASES)) {
    const base = answersFrom(s);
    const fit = evaluate(base).fit;
    const dims = profile(base);
    for (const a of combos(FIT_ONLY_IDS, base)) {
      assert.equal(fitScore(profile(a), a), fit);
      for (const d of ['A', 'S', 'C', 'G']) assert.equal(profile(a)[d], dims[d]);
    }
  }
});

test('tie-break never pushes a "free" budget reader from open source to managed', () => {
  const base = answersFrom(CASES.hobbyist);
  for (const a of combos(ROUTING_IDS, base)) {
    const f = routeForms(a);
    if (f.tie && f.tie.rule === 'managedOverOss') assert.notEqual(a.budget, 'free');
  }
});

test('a reader who would not pay is never pushed past open source to a paid-only edition', () => {
  for (const s of Object.values(CASES)) {
    for (const a of combos(ROUTING_IDS, answersFrom(s))) {
      if (a.budget !== 'free') continue;
      const f = routeForms(a);
      if (f.blocks.oss.length) continue;
      assert.ok(!['enterprise', 'private'].includes(f.top), `free reader got ${f.top}: ${encode(a)}`);
      if (f.tie) assert.notEqual(f.tie.rule, 'managedOverOss');
    }
  }
});

test('the ops-time tie-break never applies to readers who enjoy running infrastructure', () => {
  for (const a of combos(ROUTING_IDS, answersFrom(CASES.claudeArchaeologist))) {
    const f = routeForms(a);
    if (f.tie && f.tie.rule === 'managedOverOss') assert.notEqual(a.ops, 'love');
  }
});

test('personas agree with the answers that produced them', () => {
  let seed = 3;
  const rand = (n) => {
    seed = (seed + 0x6D2B79F5) | 0;
    let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return (((x ^ (x >>> 14)) >>> 0) % n);
  };
  for (let i = 0; i < 40000; i += 1) {
    const a = Object.fromEntries(QUESTIONS.map(q => [q.id, q.options[rand(q.options.length)].key]));
    const { persona } = evaluate(a);
    if (persona === 'plumber') assert.ok(['rag', 'spaghetti'].includes(a.retrieval), encode(a));
    if (persona === 'goldfish') assert.notEqual(a.reexplain, 'never', encode(a));
    if (persona === 'tinkerer') assert.equal(a.ops, 'love', encode(a));
    if (persona === 'zookeeper') assert.notEqual(a.swarm, 'single', encode(a));
  }
});

test('re-explaining context every session inside an agent client is never low fit', () => {
  for (const s of Object.values(CASES)) {
    for (const reexplain of ['essay', 'groundhog']) {
      const a = { ...answersFrom(s), reexplain, memhack: 'nothing', volume: 'prompt', retrieval: 'paste' };
      if (a.stage === 'chat') continue;
      assert.ok(evaluate(a).fit >= 45, encode(a));
    }
  }
});

test('every persona is reachable and low fit always means the traveler', () => {
  const seen = new Set();
  // mulberry32: small, deterministic, and its low bits are usable.
  let seed = 7;
  const rand = (n) => {
    seed = (seed + 0x6D2B79F5) | 0;
    let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return (((x ^ (x >>> 14)) >>> 0) % n);
  };
  for (let i = 0; i < 40000; i += 1) {
    const a = Object.fromEntries(QUESTIONS.map(q => [q.id, q.options[rand(q.options.length)].key]));
    const r = evaluate(a);
    seen.add(r.persona);
    if (r.fit < LOW_FIT) assert.equal(r.persona, 'traveler');
    assert.ok(r.fit >= 0 && r.fit <= 100);
  }
  assert.deepEqual([...seen].sort(), [...PERSONA_ORDER].sort());
});

test('what-if lines only report real changes', () => {
  const a = answersFrom(CASES.hobbyist);
  for (const w of whatIfs(a)) {
    assert.notEqual(a[w.id], w.key);
    assert.equal(w.changed, routeForms({ ...a, [w.id]: w.key }).top !== routeForms(a).top);
  }
});

test('prompts carry every answer and stay inside the public fact boundary', () => {
  const a = answersFrom(CASES.saas);
  const r = evaluate(a);
  for (const lang of ['zh', 'en']) {
    const p = promptWithAnswers(a, r, lang);
    for (let i = 1; i <= 12; i += 1) assert.ok(p.includes(`Q${i}. `));
    assert.ok(promptInterview(lang).includes(FACTS[lang]));
    for (const banned of ['99.9', '95%', '万亿', 'SOC 2', '等保', 'docs.openviking.net', '@bytedance', 'larkoffice']) {
      assert.ok(!p.includes(banned), `${lang} prompt must not mention ${banned}`);
    }
  }
});
