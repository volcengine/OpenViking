/* Scoring for the fit test. Pure functions only: the SSG build imports this
 * in node, and scoring.test.js runs it directly. Every rule here is described
 * in the post's "How the scoring works" section and in llm.txt; keep them in
 * sync when a number changes. */

import {
  QUESTIONS, QUESTION_IDS, DIM_MAX, DIM_ORDER, BANDS, FORM_ORDER,
} from './quiz-data.js';

const BY_ID = Object.fromEntries(QUESTIONS.map(q => [q.id, q]));

export const LOW_FIT = 30;
export const TIE_WINDOW = 5;
export const FORM_BASE = 35;

/* Edition routing points, added to FORM_BASE. Region, compliance, ops, models
 * and budget only ever touch these, never the fit score. */
export const ROUTE = {
  ops: {
    love: { oss: 22, personal: -18, enterprise: -12, private: -2 },
    platform: { oss: 8, personal: -8, enterprise: 4, private: 16 },
    ok: { oss: 6, personal: 8, enterprise: 10, private: -10 },
    nope: { oss: -20, personal: 18, enterprise: 18, private: -18 },
  },
  budget: {
    free: { oss: 18, personal: 2, enterprise: -18, private: -24 },
    coffee: { oss: 0, personal: 12, enterprise: -4, private: -16 },
    budget: { oss: -6, personal: 2, enterprise: 14, private: 4 },
    procure: { oss: -10, personal: -8, enterprise: 10, private: 14 },
  },
  models: {
    ark: { oss: 0, personal: 10, enterprise: 10, private: 2 },
    apikeys: { oss: 6, personal: 0, enterprise: 0, private: 0 },
    local: { oss: 10, personal: -15, enterprise: -15, private: 6 },
    gateway: { oss: 6, personal: 0, enterprise: 0, private: 10 },
  },
  share: {
    me: { oss: 2, personal: 14, enterprise: -22, private: -16 },
    fewtools: { oss: 2, personal: 14, enterprise: -22, private: -16 },
    team: { oss: -2, personal: 0, enterprise: 16, private: 4 },
    customers: { oss: -6, personal: 0, enterprise: 20, private: 8 },
  },
  stage: {
    chat: { oss: 0, personal: 0, enterprise: -10, private: -10 },
    pair: { oss: 2, personal: 4, enterprise: -4, private: -6 },
    build: { oss: 0, personal: -4, enterprise: 6, private: 0 },
    fleet: { oss: -4, personal: 0, enterprise: 8, private: 8 },
  },
  compliance: {
    who: { oss: 0, personal: 4, enterprise: 0, private: -14 },
    cloud: { oss: 0, personal: 2, enterprise: 4, private: -8 },
    account: { oss: 6, personal: 0, enterprise: 0, private: 24 },
    offline: { oss: 4, personal: 0, enterprise: 0, private: 28 },
  },
  region: {
    cn: {},
    overseas: { oss: 8, personal: -12, enterprise: -12, private: 4 },
    both: { oss: 4, personal: -6, enterprise: -6, private: 4 },
  },
};

const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

export function isComplete(answers) {
  return QUESTION_IDS.every(id => answers && answers[id]);
}

export function answeredCount(answers) {
  return QUESTION_IDS.filter(id => answers && answers[id]).length;
}

function optionOf(id, key) {
  return BY_ID[id]?.options.find(o => o.key === key) || null;
}

export function rawPoints(answers) {
  const raw = { A: 0, S: 0, C: 0, G: 0, O: 0 };
  for (const id of QUESTION_IDS) {
    const opt = optionOf(id, answers[id]);
    if (!opt) continue;
    for (const [dim, pts] of Object.entries(opt.fx)) raw[dim] += pts;
  }
  return raw;
}

export function profile(answers) {
  const raw = rawPoints(answers);
  const dims = {};
  for (const d of DIM_ORDER) dims[d] = clamp(Math.round((100 * raw[d]) / DIM_MAX[d]), 0, 100);
  return dims;
}

/* Fit: memory pain first, knowledge sprawl second, the higher of crowd and
 * control third. O (DIY drive) never counts. */
export function fitScore(dims, answers) {
  let fit = 0.5 * dims.A + 0.35 * dims.S + 0.15 * Math.max(dims.C, dims.G);
  if (dims.A >= 60 && dims.S >= 50) fit += 10;
  fit = Math.round(clamp(fit, 0, 100));
  // Re-explaining the background every session inside an agent client is the core case.
  if ((answers.reexplain === 'essay' || answers.reexplain === 'groundhog') && answers.stage !== 'chat') fit = Math.max(fit, 45);
  if (answers.reexplain === 'never') fit = Math.min(fit, 45);
  if (answers.retrieval === 'vectorsok' && dims.A < 40) fit = Math.min(fit, 35);
  if (answers.stage === 'chat' && answers.reexplain === 'never') fit = Math.min(fit, 20);
  return fit;
}

export function bandFor(fit) {
  return BANDS.find(b => fit >= b.min) || BANDS[BANDS.length - 1];
}

export function personaFor(dims, fit, answers) {
  if (fit < LOW_FIT) return 'traveler';
  const crowded = answers.share === 'team' || answers.share === 'customers' || answers.stage === 'fleet';
  if (dims.G >= 67 || (answers.models === 'gateway' && crowded && answers.compliance !== 'who')) return 'regular';
  if (answers.share === 'customers') return 'landlord';
  if (answers.swarm === 'copy') return 'bus';
  if (answers.swarm !== 'single' && dims.C >= 50 && dims.C >= dims.A && dims.C >= dims.S) return 'zookeeper';
  if (answers.memhack === 'homemade' && answers.ops === 'love') return 'tinkerer';
  if (answers.memhack === 'bigmd' && dims.A >= 40) return 'archaeologist';
  if (dims.S > dims.A) {
    return ['rag', 'spaghetti'].includes(answers.retrieval) ? 'plumber' : 'librarian';
  }
  return answers.reexplain === 'never' ? 'traveler' : 'goldfish';
}

/* Hard constraints. A blocked edition stays visible, greyed, with its reason. */
export function blocksFor(answers) {
  const blocks = { oss: [], personal: [], enterprise: [], private: [] };
  const hosted = ['personal', 'enterprise'];
  if (answers.compliance === 'account' || answers.compliance === 'offline') hosted.forEach(f => blocks[f].push('residency'));
  if (answers.models === 'gateway') hosted.forEach(f => blocks[f].push('gateway'));
  if (answers.share === 'team' || answers.share === 'customers' || answers.stage === 'fleet') blocks.personal.push('singleUser');
  return blocks;
}

/* Non-blocking notes shown under an edition. */
export function notesFor(answers) {
  const notes = { oss: [], personal: [], enterprise: [], private: [] };
  for (const f of ['personal', 'enterprise']) {
    if (answers.models && answers.models !== 'ark' && answers.models !== 'gateway') notes[f].push('arkNeeded');
    if (answers.models === 'local') notes[f].push('fixedModels');
    if (answers.region === 'overseas') notes[f].push('regionOverseas');
    if (answers.region === 'both') notes[f].push('regionBoth');
  }
  if (answers.models === 'local' || answers.models === 'gateway') notes.oss.push('ownModels');
  if (answers.compliance === 'offline' || answers.compliance === 'account') notes.oss.push('noOfficialSupport');
  if (answers.share === 'customers' || answers.stage === 'build') notes.oss.push('agpl');
  if (answers.ops === 'nope' || answers.ops === 'ok') notes.private.push('opsGap');
  if (answers.compliance === 'offline') notes.private.push('offlinePlan');
  return notes;
}

export function formScores(answers) {
  const scores = {};
  for (const f of FORM_ORDER) {
    let s = FORM_BASE;
    for (const [qid, table] of Object.entries(ROUTE)) {
      const row = table[answers[qid]];
      if (row && row[f]) s += row[f];
    }
    scores[f] = clamp(Math.round(s), 0, 100);
  }
  return scores;
}

const HOSTED = new Set(['personal', 'enterprise']);

/* Tie-break when the top two available editions are less than TIE_WINDOW
 * apart (described in the post's scoring section):
 * - a reader who wouldn't pay is never moved off open source;
 * - open source vs a managed plan: managed first, because the ops time saved
 *   is usually worth more than the bill, unless running infrastructure is the
 *   reader's idea of fun;
 * - private deployment vs anything else: private first when an SRE / platform
 *   team runs things, otherwise the other edition. */
export function tieBreak(first, second, answers) {
  const pair = new Set([first, second]);
  if (answers.budget === 'free' && pair.has('oss')) return null;
  if (pair.has('oss') && (pair.has('personal') || pair.has('enterprise'))) {
    if (answers.ops === 'love') return null;
    return { winner: HOSTED.has(first) ? first : second, rule: 'managedOverOss' };
  }
  if (pair.has('private')) {
    const other = first === 'private' ? second : first;
    return answers.ops === 'platform'
      ? { winner: 'private', rule: 'privateWithSre' }
      : { winner: other, rule: 'otherOverPrivate' };
  }
  return null;
}

export function routeForms(answers) {
  const scores = formScores(answers);
  const blocks = blocksFor(answers);
  const notes = notesFor(answers);
  const available = FORM_ORDER.filter(f => blocks[f].length === 0);
  const blocked = FORM_ORDER.filter(f => blocks[f].length > 0);
  // Stable sort: equal scores keep FORM_ORDER.
  const ranked = [...available].sort((a, b) => scores[b] - scores[a]);
  let tie = null;
  // "I wouldn't pay": Enterprise (billed from creation) and private deployment
  // (quoted) never outrank an available open-source edition.
  if (answers.budget === 'free' && ranked.includes('oss')) {
    const paid = ranked.slice(0, ranked.indexOf('oss')).filter(f => f === 'enterprise' || f === 'private');
    if (paid.length) {
      const rest = ranked.filter(f => !paid.includes(f));
      rest.splice(rest.indexOf('oss') + 1, 0, ...paid);
      ranked.splice(0, ranked.length, ...rest);
      if (ranked[0] === 'oss') tie = { winner: 'oss', rule: 'freeFirst', between: ['oss', paid[0]] };
    }
  }
  if (!tie && ranked.length >= 2 && scores[ranked[0]] - scores[ranked[1]] < TIE_WINDOW) {
    const tb = tieBreak(ranked[0], ranked[1], answers);
    if (tb) {
      tie = { ...tb, between: [ranked[0], ranked[1]] };
      if (tb.winner === ranked[1]) [ranked[0], ranked[1]] = [ranked[1], ranked[0]];
    }
  }
  return {
    scores,
    blocks,
    notes,
    ranked: [...ranked, ...blocked],
    top: ranked[0] || 'oss',
    runnerUp: ranked[1] || null,
    tie,
  };
}

export function evaluate(answers) {
  const dims = profile(answers);
  const fit = fitScore(dims, answers);
  const band = bandFor(fit);
  const persona = personaFor(dims, fit, answers);
  const forms = routeForms(answers);
  return { dims, fit, band: band.key, persona, lowFit: fit < LOW_FIT, forms };
}

/* Share code: "1" (format version) + one letter per question, a = first option. */
export function encode(answers) {
  return '1' + QUESTIONS.map(q => {
    const i = q.options.findIndex(o => o.key === answers[q.id]);
    return i < 0 ? '-' : String.fromCharCode(97 + i);
  }).join('');
}

export function decode(code, { allowPartial = false } = {}) {
  if (typeof code !== 'string' || code.length !== QUESTIONS.length + 1 || code[0] !== '1') return null;
  const answers = {};
  for (let i = 0; i < QUESTIONS.length; i += 1) {
    const ch = code[i + 1];
    if (ch === '-') {
      if (!allowPartial) return null;
      continue;
    }
    const idx = ch.charCodeAt(0) - 97;
    const opt = QUESTIONS[i].options[idx];
    if (!opt) return null;
    answers[QUESTIONS[i].id] = opt.key;
  }
  return answers;
}

/* "What if" overrides for the result page. Each entry flips one answer. */
export const WHAT_IFS = [
  { id: 'ops', key: 'nope' },
  { id: 'share', key: 'team' },
  { id: 'budget', key: 'budget' },
  { id: 'compliance', key: 'account' },
  { id: 'models', key: 'ark' },
];

// Skip "what ifs" that would describe a smaller setup than the reader already has.
const WHAT_IF_SKIP = {
  'share:team': a => a.share === 'customers',
  'budget:budget': a => a.budget === 'procure',
};

export function whatIfs(answers, limit = 3) {
  const base = routeForms(answers).top;
  const out = [];
  for (const w of WHAT_IFS) {
    if (answers[w.id] === w.key || WHAT_IF_SKIP[`${w.id}:${w.key}`]?.(answers)) continue;
    const top = routeForms({ ...answers, [w.id]: w.key }).top;
    out.push({ ...w, top, changed: top !== base });
  }
  // Answers that change the pick are the interesting ones; keep the rest as filler.
  out.sort((a, b) => Number(b.changed) - Number(a.changed));
  return out.slice(0, limit);
}
