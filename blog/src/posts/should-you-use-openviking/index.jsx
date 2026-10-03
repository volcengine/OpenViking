import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  A, Article, H2, H3, InlineCode, Lead, Li, P, Ul, useBlog,
} from '../../blog-components';
import {
  SLUG, QUESTIONS, QUESTION_IDS, DIMS, DIM_ORDER, BANDS, PERSONAS, PERSONA_ORDER, FORM_ORDER,
} from './quiz-data.js';
import {
  evaluate, encode, decode, isComplete, answeredCount, whatIfs,
} from './scoring.js';
import {
  LINKS, FORMS, FIT_REASONS, BLOCK_REASONS, NOTES, TIE_NOTES, FITS, FINE_PRINT, NUDGES,
  WHAT_IF_LABELS, LOW_FIT_TRIGGERS, INTERSTITIAL,
} from './copy.js';
import {
  promptWithAnswers, promptInterview, shareUrl, shareText, bossMemo,
} from './prompts.js';
import { trackEvent } from '../../track';

const LLM_PATH = `/post/${SLUG}/llm.txt`;
const COVER = `/assets/covers/${SLUG}.webp`;
const CARD_COVER = `/assets/covers/${SLUG}-card.webp`;
const POST_PATH = `/post/${SLUG}/`;
const STORAGE_KEY = 'syo:v1';
const LETTERS = ['A', 'B', 'C', 'D', 'E'];

// Anonymous usage events: which options people pick, what result they get, what they copy or click.
const track = (name) => trackEvent(`${SLUG}/${name}`);

function trackCompletion(answers) {
  const result = evaluate(answers);
  QUESTION_IDS.forEach(id => track(`answer/${id}/${answers[id]}`));
  track(`persona/${result.persona}`);
  track(`edition/${result.lowFit ? 'none' : result.forms.top}`);
}

// Option letters (by position, as in the share code) for questions 1..index+1; '_' marks a skipped one.
function answerPath(answers, index) {
  return QUESTIONS.slice(0, index + 1).map((q) => {
    const i = q.options.findIndex(o => o.key === answers[q.id]);
    return i < 0 ? '_' : LETTERS[i];
  }).join('');
}

// A takes no onClick, so link clicks are caught on the container and matched by href.
function linkTracker(place, links, t) {
  const handler = (e) => {
    const a = e.target.closest?.('a[href]');
    const hit = a && links.find(x => t(x.href) === a.getAttribute('href'));
    if (hit) track(`cta/${place}/${hit.id}`);
  };
  return { onClick: handler, onAuxClick: handler };
}

const reducedMotion = () => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
};

function readSharedCode() {
  if (typeof window === 'undefined') return null;
  try {
    const code = new URLSearchParams(window.location.search).get('r');
    return code ? decode(code) : null;
  } catch {
    return null;
  }
}

function scrollIntoViewIfNeeded(el) {
  if (!el) return;
  const top = el.getBoundingClientRect().top;
  if (top < 72 || top > window.innerHeight * 0.6) {
    window.scrollTo({ top: window.scrollY + top - 88, behavior: reducedMotion() ? 'auto' : 'smooth' });
  }
}

/* ---------- styles ---------- */

const CSS = `
      .syo *, .syo *::before, .syo *::after { box-sizing: border-box; }
      .syo-mono { font-family: var(--th-font-mono); }
      .syo-kicker {
        font-family: var(--th-font-mono); font-size: 11px; font-weight: 500;
        letter-spacing: 0.16em; text-transform: uppercase; color: var(--th-mute);
      }
      .syo-note { color: var(--th-mute); font-size: 14px; line-height: 1.65; }
      /* Chinese set in the mono face spreads out; use the body face for zh labels and long text. */
      html[lang="zh"] .syo :is(.syo-kicker, .syo-btn, .syo-tab, .syo-hint, .syo-details > summary, .syo-pre, .syo-ed dt, .syo-rank__score, .syo-pick__sub) {
        font-family: var(--th-font-body);
        letter-spacing: 0.04em;
      }
      html[lang="zh"] .syo .syo-pre { font-size: 13.5px; line-height: 1.8; }
      html[lang="zh"] .syo .syo-kicker { font-size: 12px; }

      /* buttons */
      .syo-btn {
        display: inline-flex; align-items: center; justify-content: center; gap: 8px;
        min-height: 40px; padding: 9px 18px; border-radius: 999px; cursor: pointer;
        border: 1px solid var(--th-ink); background: var(--th-ink); color: var(--th-bg);
        font-family: var(--th-font-mono); font-size: 13px; letter-spacing: 0.02em; line-height: 1.2;
        text-decoration: none; transition: background-color 0.15s, border-color 0.15s, color 0.15s;
      }
      .syo-btn:hover { background: var(--th-accent); border-color: var(--th-accent); color: var(--th-bg); }
      .syo-btn--ghost { background: transparent; color: var(--th-ink); border-color: var(--th-line); }
      .syo-btn--ghost:hover { background: transparent; color: var(--th-accent); border-color: var(--th-accent); }
      .syo-btn[disabled] { opacity: 0.45; cursor: not-allowed; }
      .syo-link {
        background: none; border: 0; padding: 0; cursor: pointer; color: var(--th-accent);
        font: inherit; text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px;
      }
      .syo-link:hover { text-decoration-thickness: 2px; }
      .syo :is(button, a, [tabindex]):focus-visible { outline: 2px solid var(--th-accent); outline-offset: 3px; }

      /* quiz frame */
      .syo-quiz {
        position: relative; margin: 28px 0 12px; border: 1px solid var(--th-line); border-radius: var(--th-radius);
        background: color-mix(in oklab, var(--th-bg-2) 55%, var(--th-bg)); overflow: hidden;
      }
      .syo-quiz::before {
        content: ''; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--th-accent);
      }
      .syo-quiz__pad { padding: 30px 32px 30px; }
      .syo-quiz:focus { outline: none; }

      /* intro */
      .syo-intro__title {
        margin: 14px 0 10px; font-family: var(--th-font-display); font-weight: 500;
        font-size: clamp(28px, 4vw, 38px); line-height: 1.15; color: var(--th-ink); text-wrap: balance;
      }
      .syo-intro__body { margin: 0 0 22px; max-width: 52ch; color: var(--th-ink); font-size: 17px; line-height: 1.7; }
      .syo-intro__meta { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0 0 24px; }
      .syo-intro__meta span { display: inline-flex; align-items: baseline; gap: 6px; }
      .syo-intro__meta b { font-family: var(--th-font-display); font-weight: 500; font-size: 22px; color: var(--th-ink); }
      .syo-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }

      /* question */
      .syo-qhead { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
      .syo-progress { display: grid; grid-template-columns: repeat(12, 1fr); gap: 4px; margin-bottom: 26px; }
      .syo-progress span { height: 3px; border-radius: 2px; background: var(--th-line); transition: background-color 0.2s; }
      .syo-progress span.is-done { background: color-mix(in oklab, var(--th-accent) 55%, var(--th-line)); }
      .syo-progress span.is-current { background: var(--th-accent); }
      .syo-q {
        margin: 0 0 8px; font-family: var(--th-font-display); font-weight: 500; color: var(--th-ink);
        font-size: clamp(22px, 3vw, 28px); line-height: 1.3; text-wrap: balance;
      }
      .syo-aside { margin: 0 0 22px; color: var(--th-mute); font-size: 15px; line-height: 1.6; }
      .syo-options { display: grid; gap: 10px; }
      .syo-opt {
        display: grid; grid-template-columns: 28px 1fr; align-items: start; gap: 12px; width: 100%;
        padding: 13px 16px; text-align: left; cursor: pointer; border-radius: var(--th-radius);
        border: 1px solid var(--th-line); background: var(--th-bg); color: var(--th-ink);
        font-family: var(--th-font-body); font-size: 16px; line-height: 1.55;
        transition: border-color 0.15s, background-color 0.15s, transform 0.15s;
      }
      .syo-opt:hover { border-color: var(--th-accent); }
      .syo-opt[aria-checked="true"] {
        border-color: var(--th-accent);
        background: color-mix(in oklab, var(--th-accent) 7%, var(--th-bg));
        box-shadow: inset 3px 0 0 var(--th-accent);
      }
      .syo-opt__key {
        display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; margin-top: 1px;
        border: 1px solid var(--th-line); border-radius: 999px; font-family: var(--th-font-mono); font-size: 11px;
        color: var(--th-mute); background: var(--th-bg-2);
      }
      .syo-opt[aria-checked="true"] .syo-opt__key { background: var(--th-accent); border-color: var(--th-accent); color: var(--th-bg); }
      .syo-qfoot { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 22px; }
      .syo-qfoot .syo-link, .syo-actions .syo-link { font-size: 15px; }
      .syo-hint { color: var(--th-mute); font-family: var(--th-font-mono); font-size: 11px; letter-spacing: 0.04em; }

      /* interstitial */
      .syo-calc { min-height: 260px; display: grid; place-items: center; text-align: center; }
      .syo-calc__dots { display: inline-flex; gap: 6px; margin-bottom: 16px; }
      .syo-calc__dots span { width: 6px; height: 6px; border-radius: 999px; background: var(--th-accent); animation: syo-pulse 0.9s infinite ease-in-out; }
      .syo-calc__dots span:nth-child(2) { animation-delay: 0.15s; }
      .syo-calc__dots span:nth-child(3) { animation-delay: 0.3s; }
      @keyframes syo-pulse { 0%, 100% { opacity: 0.25; transform: translateY(0); } 50% { opacity: 1; transform: translateY(-3px); } }

      /* result */
      .syo-banner {
        display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px;
        margin-bottom: 22px; padding: 12px 14px; border: 1px dashed var(--th-line); border-radius: var(--th-radius);
        font-size: 15px; line-height: 1.5;
      }
      .syo-card {
        position: relative; margin: 0 auto; padding: 26px 28px 20px; border-radius: var(--th-radius);
        background: var(--th-bg); border: 1px solid var(--th-line); box-shadow: var(--th-shadow);
      }
      .syo-card::after {
        content: ''; position: absolute; inset: 6px; border: 1px solid color-mix(in oklab, var(--th-line) 70%, transparent);
        border-radius: 1px; pointer-events: none;
      }
      .syo-card__top { display: flex; justify-content: space-between; gap: 12px; }
      .syo-card__no { color: var(--th-accent); }
      .syo-card__name {
        margin: 18px 0 6px; font-family: var(--th-font-display); font-weight: 500; color: var(--th-ink);
        font-size: clamp(28px, 4.4vw, 38px); line-height: 1.15; text-wrap: balance;
      }
      .syo-card__motto { margin: 0; font-family: var(--th-font-display); font-size: 18px; line-height: 1.5; color: var(--th-mute); }
      .syo-card__grid { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 18px 28px; align-items: center; margin: 18px 0 8px; }
      .syo-fit { display: flex; align-items: baseline; gap: 6px; font-family: var(--th-font-display); color: var(--th-ink); }
      .syo-fit b { font-weight: 500; font-size: 68px; line-height: 1; font-variant-numeric: tabular-nums; }
      .syo-fit span { font-size: 20px; color: var(--th-mute); }
      .syo-band { margin: 10px 0 4px; font-family: var(--th-font-display); font-size: 20px; font-weight: 500; color: var(--th-accent); }
      .syo-band-line { margin: 0; color: var(--th-mute); font-size: 15px; line-height: 1.6; }
      .syo-dims { display: grid; gap: 7px; margin: 14px 0 0; }
      .syo-dim { display: grid; grid-template-columns: 4.6em 1fr 2.2em; align-items: center; gap: 10px; font-size: 13px; }
      .syo-dim dt { color: var(--th-mute); }
      .syo-dim dd { margin: 0; }
      .syo-dim__bar { position: relative; height: 4px; border-radius: 2px; background: var(--th-line); overflow: hidden; }
      .syo-dim__bar i { position: absolute; inset: 0 auto 0 0; background: var(--th-accent); border-radius: 2px; }
      .syo-dim__val { font-family: var(--th-font-mono); font-size: 12px; color: var(--th-ink); text-align: right; }
      .syo-card__symptom { margin: 18px 0 16px; padding-top: 16px; border-top: 1px solid var(--th-line); font-size: 16px; line-height: 1.7; }
      .syo-card__foot { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }

      .syo-radar { display: block; width: 100%; max-width: 360px; height: auto; margin: 0 auto; overflow: visible; }
      .syo-radar .grid { fill: none; stroke: var(--th-line); stroke-width: 1; }
      .syo-radar .axis { stroke: var(--th-line); stroke-width: 1; stroke-dasharray: 2 4; }
      .syo-radar .area {
        fill: color-mix(in oklab, var(--th-accent) 18%, transparent); stroke: var(--th-accent); stroke-width: 1.6;
        stroke-linejoin: round; transform-origin: 180px 130px; animation: syo-grow 0.55s cubic-bezier(.2,.8,.2,1) both;
      }
      .syo-radar .dot { fill: var(--th-accent); stroke: var(--th-bg); stroke-width: 2; }
      .syo-radar text { font-family: var(--th-font-body); font-size: 14px; fill: var(--th-mute); }
      .syo-radar text .v { font-family: var(--th-font-mono); fill: var(--th-ink); }
      @keyframes syo-grow { from { transform: scale(0.2); opacity: 0; } to { transform: scale(1); opacity: 1; } }

      .syo-block { margin-top: 34px; }
      .syo-block__label { margin-bottom: 12px; }
      .syo-pick {
        padding: 22px 24px; border: 1px solid var(--th-line); border-left: 3px solid var(--th-accent);
        border-radius: var(--th-radius); background: var(--th-bg);
      }
      .syo-pick__name { margin: 6px 0 2px; font-family: var(--th-font-display); font-size: 26px; font-weight: 500; line-height: 1.25; color: var(--th-ink); }
      .syo-pick__sub { margin: 0 0 12px; color: var(--th-mute); font-size: 13px; font-family: var(--th-font-mono); }
      .syo-pick__reason { margin: 0 0 10px; font-size: 17px; line-height: 1.65; }
      .syo-pick__needs { margin: 0 0 16px; color: var(--th-mute); font-size: 14px; line-height: 1.65; }
      .syo-pick__needs b { color: var(--th-ink); font-weight: 500; }
      .syo-tie {
        margin: 0 0 16px; padding: 10px 12px; border-radius: var(--th-radius); font-size: 14px; line-height: 1.6;
        background: color-mix(in oklab, var(--th-accent) 7%, transparent); color: var(--th-ink);
      }
      .syo-cta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; }
      .syo-cta a.syo-ext { font-size: 14px; }
      .syo-notes { margin: 14px 0 0; padding: 0; list-style: none; color: var(--th-mute); font-size: 14px; line-height: 1.6; }
      .syo-notes li { position: relative; padding-left: 14px; margin: 4px 0; }
      .syo-notes li::before { content: '·'; position: absolute; left: 2px; color: var(--th-accent); }

      .syo-rank { margin: 14px 0 0; padding: 0; list-style: none; display: grid; gap: 4px; }
      .syo-rank li { display: grid; grid-template-columns: minmax(0, 11em) minmax(0, 1fr) 3.4em; align-items: center; gap: 14px; padding: 9px 0; border-bottom: 1px solid var(--th-line); }
      .syo-rank li:last-child { border-bottom: 0; }
      .syo-rank__name { font-size: 15px; color: var(--th-ink); }
      .syo-rank__bar { height: 6px; border-radius: 3px; background: color-mix(in oklab, var(--th-line) 70%, transparent); overflow: hidden; }
      .syo-rank__bar i { display: block; height: 100%; border-radius: 3px; background: color-mix(in oklab, var(--th-accent) 45%, transparent); }
      .syo-rank li.is-top .syo-rank__bar i { background: var(--th-accent); }
      .syo-rank__score { font-family: var(--th-font-mono); font-size: 13px; text-align: right; }
      .syo-rank li.is-blocked .syo-rank__name, .syo-rank li.is-blocked .syo-rank__score { color: var(--th-mute); }
      .syo-rank__why { grid-column: 2 / -1; margin-top: -4px; color: var(--th-mute); font-size: 13px; line-height: 1.55; }
      .syo-rank li.is-blocked .syo-rank__bar { background: repeating-linear-gradient(135deg, var(--th-line) 0 2px, transparent 2px 6px); }

      .syo-nudge {
        margin: 18px 0 0; padding: 2px 0 2px 14px; border-left: 2px solid var(--th-tip);
        color: var(--th-ink); font-size: 15px; line-height: 1.65;
      }
      .syo-why { display: grid; grid-template-columns: 1fr 1fr; gap: 18px 28px; }
      .syo-why ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
      .syo-why li { position: relative; padding-left: 22px; font-size: 15px; line-height: 1.65; }
      .syo-why li::before { position: absolute; left: 0; top: 0; font-family: var(--th-font-mono); color: var(--th-accent); }
      .syo-why .is-pro li::before { content: '+'; }
      .syo-why .is-con li::before { content: '·'; color: var(--th-mute); }

      .syo-whatif { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
      .syo-whatif li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; font-size: 15px; line-height: 1.6; }
      .syo-whatif .res { display: inline-flex; gap: 8px; }
      .syo-whatif .arrow { color: var(--th-mute); font-family: var(--th-font-mono); }
      .syo-whatif .same { color: var(--th-mute); }

      .syo-lowfit h4, .syo-lowfit__title { margin: 6px 0 8px; font-family: var(--th-font-display); font-weight: 500; font-size: 26px; line-height: 1.25; color: var(--th-ink); }
      .syo-lowfit ol { margin: 10px 0 18px; padding-left: 1.3em; }
      .syo-lowfit li { margin: 4px 0; font-size: 15px; line-height: 1.6; }

      .syo-details { margin-top: 18px; border: 1px solid var(--th-line); border-radius: var(--th-radius); background: var(--th-bg); }
      .syo-details > summary {
        display: flex; align-items: center; gap: 10px; padding: 12px 16px; cursor: pointer; list-style: none;
        font-family: var(--th-font-mono); font-size: 12.5px; letter-spacing: 0.02em; color: var(--th-ink);
      }
      .syo-details > summary::-webkit-details-marker { display: none; }
      .syo-details > summary::before {
        content: '+'; display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px;
        border: 1px solid var(--th-line); border-radius: 999px; color: var(--th-accent); flex: 0 0 auto;
      }
      .syo-details[open] > summary::before { content: '−'; }
      .syo-details__body { padding: 0 16px 16px; }
      .syo-pre {
        margin: 0; max-height: 340px; overflow: auto; padding: 14px 16px; border: 1px solid var(--th-line);
        border-radius: var(--th-radius); background: var(--th-bg-2); color: var(--th-ink);
        font-family: var(--th-font-mono); font-size: 12.5px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere;
      }
      .syo-fallback { margin-top: 10px; }
      .syo-fallback textarea {
        width: 100%; min-height: 140px; padding: 10px 12px; border: 1px solid var(--th-accent); border-radius: var(--th-radius);
        background: var(--th-bg); color: var(--th-ink); font-family: var(--th-font-mono); font-size: 12px; line-height: 1.6;
      }
      .syo-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

      /* prompt box */
      .syo-prompt { margin: 22px 0 8px; border: 1px solid var(--th-line); border-radius: var(--th-radius); background: color-mix(in oklab, var(--th-bg-2) 55%, var(--th-bg)); }
      .syo-tabs { display: flex; gap: 0; border-bottom: 1px solid var(--th-line); overflow-x: auto; scrollbar-width: none; }
      .syo-tabs::-webkit-scrollbar { display: none; }
      .syo-tab {
        flex: 0 0 auto; padding: 13px 18px; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px;
        background: transparent; cursor: pointer; color: var(--th-mute); font-family: var(--th-font-mono); font-size: 12.5px; letter-spacing: 0.02em;
      }
      .syo-tab[aria-selected="true"] { color: var(--th-ink); border-bottom-color: var(--th-accent); }
      .syo-tab.is-pending { opacity: 0.6; }
      .syo-prompt__body { padding: 18px 18px 20px; }
      .syo-prompt__intro { margin: 0 0 14px; font-size: 15px; line-height: 1.65; }
      .syo-prompt__foot { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; margin-top: 14px; }

      /* field guide */
      .syo-guide { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 22px 0 8px; }
      .syo-mini { padding: 16px 18px; border: 1px solid var(--th-line); border-radius: var(--th-radius); background: var(--th-bg); }
      .syo-mini.is-you { border-color: var(--th-accent); box-shadow: inset 0 0 0 1px var(--th-accent); }
      .syo-mini__top { display: flex; justify-content: space-between; gap: 10px; }
      .syo-mini__name { margin: 8px 0 4px; font-family: var(--th-font-display); font-size: 19px; font-weight: 500; line-height: 1.3; color: var(--th-ink); }
      .syo-mini__motto { margin: 0; color: var(--th-mute); font-size: 14px; line-height: 1.6; }
      .syo-you { color: var(--th-accent); }

      /* editions */
      .syo-editions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 22px 0 10px; }
      .syo-ed { display: flex; flex-direction: column; padding: 20px 20px 18px; border: 1px solid var(--th-line); border-radius: var(--th-radius); background: var(--th-bg); }
      .syo-ed.is-pick { border-color: var(--th-accent); box-shadow: inset 0 3px 0 var(--th-accent); }
      .syo-ed__name { margin: 8px 0 2px; font-family: var(--th-font-display); font-size: 22px; font-weight: 500; line-height: 1.25; color: var(--th-ink); }
      .syo-ed__tag { margin: 6px 0 14px; font-size: 15px; line-height: 1.6; color: var(--th-ink); }
      .syo-ed dl { margin: 0 0 16px; display: grid; gap: 10px; flex: 1; }
      .syo-ed dt { font-family: var(--th-font-mono); font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--th-mute); }
      .syo-ed dd { margin: 2px 0 0; font-size: 14px; line-height: 1.6; }
      .syo-ed__links { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 14px; padding-top: 12px; border-top: 1px solid var(--th-line); }

      @media (max-width: 720px) {
        .syo-quiz__pad { padding: 24px 18px 22px; }
        .syo-card { padding: 22px 18px 16px; }
        .syo-card__grid { grid-template-columns: 1fr; }
        .syo-fit b { font-size: 56px; }
        .syo-pick { padding: 18px 16px; }
        .syo-pick__name { font-size: 22px; }
        .syo-why { grid-template-columns: 1fr; }
        .syo-guide, .syo-editions { grid-template-columns: 1fr; }
        .syo-rank li { grid-template-columns: minmax(0, 1fr) 3.4em; gap: 6px 12px; }
        .syo-rank__bar { grid-column: 1 / -1; grid-row: 2; }
        .syo-rank__why { grid-column: 1 / -1; grid-row: 3; margin-top: 0; }
        .syo-opt { font-size: 15.5px; padding: 12px 14px; gap: 10px; }
        .syo-q { font-size: 21px; text-wrap: pretty; }
        .syo-tabs { overflow-x: visible; }
        .syo-tab { flex: 1 1 0; min-width: 0; padding: 12px 10px; white-space: normal; text-align: center; line-height: 1.35; }
        .syo-hint { display: none; }
      }
      @media (prefers-reduced-motion: reduce) {
        .syo-radar .area, .syo-calc__dots span { animation: none; }
        .syo-opt, .syo-btn { transition: none; }
      }
`;

// Raw CSS: a text child of <style> gets HTML-escaped in renderToStaticMarkup.
function Styles() {
  return <style dangerouslySetInnerHTML={{ __html: CSS }} />;
}

/* ---------- clipboard ---------- */

async function writeClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function CopyButton({ getText, label, done, ghost = true, t, event }) {
  const [state, setState] = useState('idle');
  const [fallback, setFallback] = useState('');
  const timer = useRef(null);
  const areaRef = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (fallback && areaRef.current) {
      areaRef.current.focus();
      areaRef.current.select();
    }
  }, [fallback]);
  const onClick = async () => {
    if (event) track(`copy/${event}`);
    const text = getText();
    const ok = await writeClipboard(text);
    if (ok) {
      setFallback('');
      setState('done');
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setState('idle'), 1800);
    } else {
      setFallback(text);
    }
  };
  return (
    <>
      <button type="button" className={`syo-btn ${ghost ? 'syo-btn--ghost' : ''}`} onClick={onClick}>
        {state === 'done' ? `${t(done || { zh: '已复制', en: 'Copied' })} ✓` : t(label)}
      </button>
      <span className="syo-sr" aria-live="polite">{state === 'done' ? t({ zh: '已复制到剪贴板', en: 'Copied to clipboard' }) : ''}</span>
      {fallback ? (
        <div className="syo-fallback" style={{ flexBasis: '100%' }}>
          <textarea ref={areaRef} readOnly value={fallback} aria-label={t({ zh: '要复制的文字', en: 'Text to copy' })} />
          <div className="syo-note">{t({ zh: '浏览器没让我们直接复制。文字已经选中，按 ⌘C 或 Ctrl+C 即可。', en: "Your browser blocked direct copying. The text is selected; press ⌘C or Ctrl+C." })}</div>
        </div>
      ) : null}
    </>
  );
}

/* ---------- radar ---------- */

const RADAR = { cx: 180, cy: 130, r: 86 };

function radarPoint(i, value) {
  const angle = (-90 + i * 72) * (Math.PI / 180);
  const r = (RADAR.r * value) / 100;
  return [RADAR.cx + r * Math.cos(angle), RADAR.cy + r * Math.sin(angle)];
}

function Radar({ dims, t }) {
  const pts = DIM_ORDER.map((d, i) => radarPoint(i, Math.max(dims[d], 4)));
  const label = DIM_ORDER.map(d => `${t(DIMS[d].name)} ${dims[d]}`).join(', ');
  return (
    <svg className="syo-radar" viewBox="0 0 360 262" role="img" aria-label={label}>
      {[25, 50, 75, 100].map(level => (
        <polygon
          key={level}
          className="grid"
          points={DIM_ORDER.map((_, i) => radarPoint(i, level).join(',')).join(' ')}
        />
      ))}
      {DIM_ORDER.map((_, i) => {
        const [x, y] = radarPoint(i, 100);
        return <line key={i} className="axis" x1={RADAR.cx} y1={RADAR.cy} x2={x} y2={y} />;
      })}
      <polygon className="area" points={pts.map(p => p.join(',')).join(' ')} />
      {pts.map(([x, y], i) => <circle key={i} className="dot" cx={x} cy={y} r="3.2" />)}
      {DIM_ORDER.map((d, i) => {
        const [x, y] = radarPoint(i, 128);
        const anchor = Math.abs(x - RADAR.cx) < 4 ? 'middle' : (x > RADAR.cx ? 'start' : 'end');
        const dy = i === 0 ? -4 : (y > RADAR.cy ? 12 : 4);
        return (
          <text key={d} x={x} y={y + dy} textAnchor={anchor}>
            {t(DIMS[d].short)} <tspan className="v">{dims[d]}</tspan>
          </text>
        );
      })}
    </svg>
  );
}

/* ---------- persona card ---------- */

function PersonaCard({ result, t }) {
  const persona = PERSONAS[result.persona];
  const band = BANDS.find(b => b.key === result.band);
  return (
    <div className="syo-card">
      <div className="syo-card__top">
        <span className="syo-kicker syo-card__no">No. {persona.no}</span>
        <span className="syo-kicker">{t({ zh: 'OpenViking 适配测试', en: 'OpenViking fit test' })}</span>
      </div>
      <div className="syo-card__name" tabIndex={-1} data-result-focus>{t(persona.name)}</div>
      <p className="syo-card__motto">{t(persona.motto)}</p>
      <div className="syo-card__grid">
        <Radar dims={result.dims} t={t} />
        <div>
          <div className="syo-kicker">{t({ zh: '适配度', en: 'Fit' })}</div>
          <div className="syo-fit"><b>{result.fit}</b><span>/ 100</span></div>
          <div className="syo-band">{t(band.title)}</div>
          <p className="syo-band-line">{t(band.line)}</p>
          <dl className="syo-dims">
            {DIM_ORDER.map(d => (
              <div className="syo-dim" key={d}>
                <dt>{t(DIMS[d].name)}</dt>
                <dd className="syo-dim__bar" aria-hidden="true"><i style={{ width: `${result.dims[d]}%` }} /></dd>
                <dd className="syo-dim__val">{result.dims[d]}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <p className="syo-card__symptom">{t(persona.symptom)}</p>
      <div className="syo-card__foot">
        <span className="syo-kicker">blog.openviking.ai</span>
      </div>
    </div>
  );
}

/* ---------- edition recommendation ---------- */

function Ext({ href, children }) {
  const { t } = useBlog();
  return <A href={t(href)}>{children}</A>;
}

function PrimaryCta({ form, t }) {
  const [first, ...rest] = FORMS[form].cta;
  return (
    <div className="syo-cta" {...linkTracker(`result/${form}`, FORMS[form].cta, t)}>
      <a className="syo-btn" href={t(first.href)} target="_blank" rel="noreferrer">{t(first.label)} ↗</a>
      {rest.map(c => <span key={t(c.label)} className="syo-ext"><Ext href={c.href}>{t(c.label)}</Ext></span>)}
    </div>
  );
}

function nudgeFor(result, answers) {
  const f = result.forms;
  const top = f.top;
  if (top === 'oss') {
    const out = [];
    if (!f.blocks.personal.length) {
      out.push(NUDGES.personalTrial);
      return out;
    }
    if (!f.blocks.enterprise.length) return [NUDGES.enterpriseLater];
    return [];
  }
  if (top === 'personal') return [NUDGES.personalUpgrade];
  if (top === 'enterprise') return f.blocks.personal.length ? [] : [NUDGES.enterpriseTrial];
  if (top === 'private') return [NUDGES.privatePilot];
  return [];
}

function tieNote(tie, t) {
  if (!tie) return null;
  const other = tie.between.find(k => k !== tie.winner) || tie.between[1];
  return t(TIE_NOTES[tie.rule](FORMS[tie.winner].name, FORMS[other].name));
}

function EditionPick({ result, answers, t }) {
  const f = result.forms;
  const top = f.top;
  const form = FORMS[top];
  const nudges = nudgeFor(result, answers);
  return (
    <>
      <div className="syo-pick">
        <div className="syo-kicker">{t(result.band === 'later' ? { zh: '如果现在就试', en: 'If you try it now' } : { zh: '推荐方案', en: 'Recommended for you' })}</div>
        <div className="syo-pick__name">{t(form.name)}</div>
        <p className="syo-pick__sub">{t(form.sub)}</p>
        <p className="syo-pick__reason">{t(FIT_REASONS[top])}</p>
        <p className="syo-pick__needs"><b>{t({ zh: '需要准备：', en: 'You will need: ' })}</b>{t(form.needs)}<br /><b>{t({ zh: '费用：', en: 'Cost: ' })}</b>{t(form.cost)}</p>
        {f.tie && f.tie.winner === top ? <p className="syo-tie">{tieNote(f.tie, t)}</p> : null}
        <PrimaryCta form={top} t={t} />
        {f.notes[top].length ? (
          <ul className="syo-notes">{f.notes[top].map(n => <li key={n}>{t(NOTES[n])}</li>)}</ul>
        ) : null}
      </div>
      <EditionRanking result={result} t={t} />
      {nudges.map((n, i) => <p className="syo-nudge" key={i}>{t(n)}</p>)}
    </>
  );
}

function EditionRanking({ result, t }) {
  const f = result.forms;
  return (
    <ol className="syo-rank" aria-label={t({ zh: '四种方案的匹配度', en: 'Match for each edition' })}>
      {f.ranked.map((k, i) => {
        const blocked = f.blocks[k].length > 0;
        let why = blocked
          ? f.blocks[k].map(b => t(BLOCK_REASONS[b])).join(' ')
          : (k === f.top || f.scores[k] < 40 ? '' : f.notes[k].slice(0, 1).map(n => t(NOTES[n])).join(' '));
        // Both managed plans are usually blocked for the same reason; say it once.
        const prev = f.ranked[i - 1];
        if (blocked && prev && f.blocks[prev].join() === f.blocks[k].join()) why = t({ zh: '原因同上。', en: 'Same reason as above.' });
        return (
          <li key={k} className={`${k === f.top ? 'is-top' : ''} ${blocked ? 'is-blocked' : ''}`}>
            <span className="syo-rank__name">{t(FORMS[k].name)}</span>
            <span className="syo-rank__bar" aria-hidden="true">{blocked ? null : <i style={{ width: `${Math.max(f.scores[k], 2)}%` }} />}</span>
            <span className="syo-rank__score">{blocked ? t({ zh: '不适用', en: 'not a fit' }) : f.scores[k]}</span>
            {why ? <span className="syo-rank__why">{why}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/* ---------- why / fine print / what if ---------- */

export function fitLinesFor(answers, lang) {
  return FITS.filter(f => f.when(answers)).slice(0, 4).map(f => f[lang] || f.en);
}

// The memo leads with the problem that defines the reader's type.
const PERSONA_FIT = {
  goldfish: 'reexplain', archaeologist: 'bigmd', plumber: 'retrieval', librarian: 'volume', bus: 'swarm',
  zookeeper: 'swarm', landlord: 'isolation', regular: 'residency', tinkerer: 'homemade',
};

function problemLinesFor(answers, persona, lang) {
  const hits = FITS.filter(f => f.when(answers));
  const lead = hits.find(f => f.id === PERSONA_FIT[persona]);
  const first = lead ? (lead.memo[lang] || lead.memo.en) : (PERSONAS[persona].pain[lang] || PERSONAS[persona].pain.en);
  const rest = hits.filter(f => f !== lead).map(f => f.memo[lang] || f.memo.en);
  return [first, ...rest].slice(0, 3);
}

function finePrintFor(answers) {
  const specific = FINE_PRINT.filter(f => f.when(answers) && f.id !== 'wiring' && f.id !== 'asyncRecall');
  const general = FINE_PRINT.filter(f => f.id === 'wiring' || f.id === 'asyncRecall');
  return [...specific, ...general].slice(0, 2);
}

function WhyLists({ answers, t, lang }) {
  const pros = fitLinesFor(answers, lang);
  const cons = finePrintFor(answers);
  return (
    <div className="syo-why">
      <div className="is-pro">
        <div className="syo-kicker syo-block__label">{t({ zh: '对得上的地方', en: 'Where it fits' })}</div>
        {pros.length ? <ul>{pros.map((p, i) => <li key={i}>{p}</li>)}</ul> : <p className="syo-note">{t({ zh: '你的答案里暂时没有明显的痛点。', en: 'No strong pain points in your answers yet.' })}</p>}
      </div>
      <div className="is-con">
        <div className="syo-kicker syo-block__label">{t({ zh: '使用须知', en: 'Good to know' })}</div>
        <ul>{cons.map(c => <li key={c.id}>{t(c)}</li>)}</ul>
      </div>
    </div>
  );
}

function WhatIfList({ answers, t }) {
  const items = whatIfs(answers);
  if (!items.length) return null;
  return (
    <ul className="syo-whatif">
      {items.map(w => (
        <li key={`${w.id}:${w.key}`}>
          <span>{t({ zh: `如果${WHAT_IF_LABELS[`${w.id}:${w.key}`].zh}`, en: WHAT_IF_LABELS[`${w.id}:${w.key}`].en })}</span>
          <span className="res">
            <span className="arrow">→</span>
            {w.changed
              ? <span>{t({ zh: '推荐会变成', en: 'the pick becomes' })} <strong>{t(FORMS[w.top].name)}</strong></span>
              : <span className="same">{t({ zh: '推荐不变', en: 'the pick stays the same' })}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ---------- result ---------- */

// The one-pager asks for a trial, so it only appears from the "worth an afternoon" band up.
const MEMO_MIN_FIT = BANDS.find(b => b.key === 'worth').min;

function showMemo(answers) {
  return answers.share === 'team' || answers.share === 'customers' || answers.stage === 'fleet'
    || answers.stage === 'build' || answers.budget === 'budget' || answers.budget === 'procure';
}

const LOW_FIT_LINKS = [{ id: 'github', href: LINKS.github }, { id: 'quickstart', href: LINKS.quickstart }];

function LowFit({ result, answers, t }) {
  const chatPain = answers.stage === 'chat' && (answers.reexplain === 'essay' || answers.reexplain === 'groundhog');
  return (
    <div className="syo-pick syo-lowfit">
      <div className="syo-kicker">{t({ zh: '结论', en: 'Verdict' })}</div>
      <div className="syo-lowfit__title">{t({ zh: '你暂时不需要 OpenViking', en: "You don't need OpenViking yet" })}</div>
      <p className="syo-pick__reason">{t(chatPain ? {
        zh: '健忘是真的，不过 OpenViking 接在 agent 客户端或你自己的应用上，网页聊天框里用不上。等你开始用 Claude Code、Cursor 这类客户端，或者出现下面任何一种情况，就可以回来再测一次：',
        en: "The forgetting is real, but OpenViking plugs into an agent client or your own app, not a web chat box. Once you're on a client like Claude Code or Cursor, or any of these happens, come back and retake the test:",
      } : {
        zh: '你的 agent 现在还用不上长期记忆，等需要的时候再接入就好。出现下面任何一种情况，就可以回来再测一次：',
        en: "Your agents don't need long-term memory right now, so there's no rush to add it. Come back and retake the test when any of these happens:",
      })}</p>
      <ol>{LOW_FIT_TRIGGERS.map((x, i) => <li key={i}>{t(x)}</li>)}</ol>
      <div className="syo-cta" {...linkTracker('result/none', LOW_FIT_LINKS, t)}>
        <a className="syo-btn" href={LINKS.github} target="_blank" rel="noreferrer">{t({ zh: '先给仓库点个 Star', en: 'Star the repo for later' })} ↗</a>
        <span className="syo-ext"><Ext href={LINKS.quickstart}>{t({ zh: '想先摸摸看：快速开始', en: 'Curious anyway: Quickstart' })}</Ext></span>
      </div>
      <details className="syo-details">
        <summary>{t({ zh: '如果哪天用得上：四种方案的匹配度', en: 'If you ever need it: match for each edition' })}</summary>
        <div className="syo-details__body"><EditionRanking result={result} t={t} /></div>
      </details>
    </div>
  );
}

function ResultView({ answers, result, shared, onRetake, onAskAi, t, lang }) {
  const code = encode(answers);
  const url = () => shareUrl(code);
  const memoOn = result.fit >= MEMO_MIN_FIT && showMemo(answers);
  const persona = PERSONAS[result.persona];
  return (
    <div>
      {shared ? (
        <div className="syo-banner">
          <span>{t({ zh: `你正在看别人的测试结果：${persona.name.zh}。`, en: `You're looking at someone else's result: ${persona.name.en}.` })}</span>
          <button type="button" className="syo-link" onClick={onRetake}>{t({ zh: '我也测一下 →', en: 'Take it myself →' })}</button>
        </div>
      ) : null}

      <PersonaCard result={result} t={t} />

      <div className="syo-actions" style={{ marginTop: 18 }}>
        <CopyButton t={t} event="result-link" label={{ zh: '复制结果链接', en: 'Copy result link' }} getText={url} />
        <CopyButton t={t} event="result-text" label={{ zh: '复制结果文字', en: 'Copy as text' }} getText={() => shareText(result, url(), lang)} />
        <button type="button" className="syo-link" onClick={onAskAi}>{t({ zh: '让 AI 再评估一次 ↓', en: 'Ask your AI ↓' })}</button>
        <button type="button" className="syo-link" onClick={onRetake}>{t({ zh: '重新测', en: 'Retake' })}</button>
      </div>

      <div className="syo-block">
        <div className="syo-kicker syo-block__label">{t({ zh: '该用哪个方案', en: 'Which edition' })}</div>
        {result.lowFit ? <LowFit result={result} answers={answers} t={t} /> : <EditionPick result={result} answers={answers} t={t} />}
      </div>

      <div className="syo-block">
        <WhyLists answers={answers} t={t} lang={lang} />
      </div>

      {!result.lowFit ? (
        <div className="syo-block">
          <div className="syo-kicker syo-block__label">{t({ zh: '换个答案会怎样', en: 'What would change it' })}</div>
          <WhatIfList answers={answers} t={t} />
        </div>
      ) : null}

      {memoOn ? (
        <details className="syo-details syo-block">
          <summary>{t({ zh: '给老板的一页纸', en: 'A one-pager for your boss' })}</summary>
          <div className="syo-details__body">
            <p className="syo-note" style={{ margin: '0 0 12px' }}>{t({ zh: '我们删掉了所有形容词。老板一般喜欢这样。', en: 'We removed the adjectives. Bosses tend to like that.' })}</p>
            <pre className="syo-pre">{bossMemo({ result, problems: problemLinesFor(answers, result.persona, lang), url: shareUrl(code), lang })}</pre>
            <div className="syo-prompt__foot">
              <CopyButton t={t} ghost={false} event="memo" label={{ zh: '复制这页纸', en: 'Copy the one-pager' }} getText={() => bossMemo({ result, problems: problemLinesFor(answers, result.persona, lang), url: url(), lang })} />
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
}

/* ---------- quiz ---------- */

function useQuiz(navigate) {
  const [init] = useState(() => readSharedCode());
  const [answers, setAnswers] = useState(() => init || {});
  const [step, setStep] = useState(() => (init ? 'result' : 'intro'));
  const [shared, setShared] = useState(() => Boolean(init));
  const [saved, setSaved] = useState(null);
  const advanceTimer = useRef(null);
  // Report each run's answers once, on its first completion.
  const reported = useRef(false);

  useEffect(() => {
    if (init) track('shared-view');
  }, [init]);

  useEffect(() => {
    if (init) return;
    try {
      const partial = decode(window.localStorage.getItem(STORAGE_KEY) || '', { allowPartial: true });
      if (partial && answeredCount(partial) > 0) setSaved(partial);
    } catch {
      // storage can be unavailable; resuming is a convenience only
    }
  }, [init]);

  useEffect(() => {
    if (shared || step === 'intro') return;
    try {
      window.localStorage.setItem(STORAGE_KEY, encode(answers));
    } catch {
      // ignore
    }
  }, [answers, shared, step]);

  useEffect(() => () => clearTimeout(advanceTimer.current), []);

  // Browser back/forward onto a shared link shows that result again.
  useEffect(() => {
    const onPop = () => {
      const code = readSharedCode();
      if (!code) return;
      clearTimeout(advanceTimer.current);
      setAnswers(code);
      setShared(true);
      setSaved(null);
      setStep('result');
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const firstOpen = (a) => {
    const i = QUESTION_IDS.findIndex(id => !a[id]);
    return i < 0 ? 0 : i;
  };

  const start = () => {
    clearTimeout(advanceTimer.current);
    reported.current = false;
    track('start');
    setAnswers({});
    setShared(false);
    setSaved(null);
    setStep(0);
  };

  const resume = () => {
    if (!saved) return start();
    reported.current = isComplete(saved);
    setAnswers(saved);
    setShared(false);
    setStep(isComplete(saved) ? 'result' : firstOpen(saved));
    setSaved(null);
  };

  const finish = (done) => {
    if (!reported.current) {
      reported.current = true;
      trackCompletion(done);
    }
    if (reducedMotion()) {
      setStep('result');
      return;
    }
    setStep('calc');
    advanceTimer.current = setTimeout(() => setStep('result'), 900);
  };

  const choose = (index, key) => {
    const id = QUESTION_IDS[index];
    const wasComplete = isComplete(answers);
    const next = { ...answers, [id]: key };
    // First answer to each question reports the answer path so far as option letters, e.g. seq/BCA.
    // Per-page totals then give reach per question (sum by length) and drop-off after any path prefix.
    if (!answers[id]) track(`seq/${answerPath(next, index)}`);
    setAnswers(next);
    clearTimeout(advanceTimer.current);
    const go = () => {
      // Editing an earlier answer on a finished quiz goes straight back to the result.
      if (wasComplete) finish(next);
      else if (index < QUESTIONS.length - 1) setStep(index + 1);
      else if (isComplete(next)) finish(next);
      else setStep(firstOpen(next));
    };
    if (reducedMotion()) go();
    else advanceTimer.current = setTimeout(go, 240);
  };

  const back = (index) => {
    clearTimeout(advanceTimer.current);
    if (index > 0) {
      setStep(index - 1);
      return;
    }
    // Keep progress resumable when stepping back to the intro.
    if (answeredCount(answers) > 0) setSaved(answers);
    setStep('intro');
  };

  const retake = () => {
    clearTimeout(advanceTimer.current);
    try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
    // Drop only ?r=; keep ?lang= and anything else on the URL.
    if (shared || /[?&]r=/.test(window.location.search)) navigate({ query: { r: null } });
    reported.current = false;
    track('start');
    setAnswers({});
    setShared(false);
    setSaved(null);
    setStep(0);
  };

  return { answers, step, shared, saved, start, resume, choose, back, retake };
}

function Intro({ quiz, onAskAi, t }) {
  const n = quiz.saved ? answeredCount(quiz.saved) : 0;
  const done = n === QUESTIONS.length;
  return (
    <div className="syo-quiz__pad">
      <div className="syo-kicker">{t({ zh: 'OpenViking 适配测试 · 12 题 · 约 3 分钟', en: 'OpenViking fit test · 12 questions · about 3 min' })}</div>
      <div className="syo-intro__title">{t({ zh: '你是哪种 Agent 饲养员？', en: 'What kind of agent wrangler are you?' })}</div>
      <p className="syo-intro__body">{t({
        zh: '没有标准答案，选最接近你现在状态的那个。“以后打算这样”不算，测的是现在。',
        en: "No right answers. Pick what's true today, not what's on the roadmap.",
      })}</p>
      <div className="syo-intro__meta syo-note">
        <span><b>5</b>{t({ zh: '个维度的画像', en: 'profile axes' })}</span>
        <span><b>10</b>{t({ zh: '种饲养员类型', en: 'wrangler types' })}</span>
        <span><b>4</b>{t({ zh: '种方案的匹配度', en: 'editions ranked' })}</span>
      </div>
      <div className="syo-actions">
        {n > 0 ? (
          <>
            <button type="button" className="syo-btn" onClick={quiz.resume}>{done
              ? t({ zh: '查看上次的结果', en: 'See your last result' })
              : t({ zh: `继续上次（已答 ${n} 题）`, en: `Resume (${n} answered)` })} →</button>
            <button type="button" className="syo-btn syo-btn--ghost" onClick={quiz.start}>{done ? t({ zh: '重新测', en: 'Retake' }) : t({ zh: '重新开始', en: 'Start over' })}</button>
          </>
        ) : (
          <button type="button" className="syo-btn" onClick={quiz.start}>{t({ zh: '开始测试', en: 'Start the test' })} →</button>
        )}
        <button type="button" className="syo-link" onClick={onAskAi}>{t({ zh: '不想答题？让 AI 来问你 ↓', en: 'Rather not click? Let your AI ask ↓' })}</button>
      </div>
    </div>
  );
}

function QuestionCard({ index, quiz, t }) {
  const q = QUESTIONS[index];
  const current = quiz.answers[q.id];
  const onKeyDown = (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    let i = -1;
    if (/^[1-5]$/.test(k)) i = Number(k) - 1;
    else if (/^[a-e]$/.test(k)) i = k.charCodeAt(0) - 97;
    if (i >= 0 && q.options[i]) {
      e.preventDefault();
      quiz.choose(index, q.options[i].key);
    } else if (k === 'arrowleft' || k === 'backspace') {
      e.preventDefault();
      quiz.back(index);
    } else if (k === 'arrowright' && current) {
      e.preventDefault();
      quiz.choose(index, current);
    }
  };
  return (
    <div className="syo-quiz__pad" onKeyDown={onKeyDown}>
      <div className="syo-qhead">
        <span className="syo-kicker">{t({ zh: `第 ${index + 1} 题 / 共 12 题`, en: `Question ${index + 1} of 12` })}</span>
        <span className="syo-hint">{t({ zh: '按 1–5 或 A–E 选择 · ← 返回', en: 'Keys 1–5 or A–E · ← back' })}</span>
      </div>
      <div className="syo-progress" aria-hidden="true">
        {QUESTIONS.map((x, i) => (
          <span key={x.id} className={i === index ? 'is-current' : (quiz.answers[x.id] ? 'is-done' : '')} />
        ))}
      </div>
      <p className="syo-q" id={`syo-q-${q.id}`}>{t(q.q)}</p>
      <p className="syo-aside">{t(q.aside)}</p>
      <div className="syo-options" role="radiogroup" aria-labelledby={`syo-q-${q.id}`}>
        {q.options.map((o, i) => (
          <button
            key={o.key}
            type="button"
            role="radio"
            aria-checked={current === o.key}
            className="syo-opt"
            data-autofocus={i === 0 ? 'true' : undefined}
            onClick={() => quiz.choose(index, o.key)}
          >
            <span className="syo-opt__key" aria-hidden="true">{LETTERS[i]}</span>
            <span>{t(o.t)}</span>
          </button>
        ))}
      </div>
      <div className="syo-qfoot">
        <button type="button" className="syo-link" onClick={() => quiz.back(index)}>{index === 0 ? t({ zh: '← 回到开头', en: '← Back to start' }) : t({ zh: '← 上一题', en: '← Previous' })}</button>
        <span className="syo-kicker">{t({ zh: `还剩约 ${Math.max(1, Math.ceil(((12 - index) * 14) / 60))} 分钟`, en: `About ${Math.max(1, Math.ceil(((12 - index) * 14) / 60))} min left` })}</span>
      </div>
    </div>
  );
}

function Calculating({ answers, t }) {
  const idx = encode(answers).split('').reduce((s, c) => s + c.charCodeAt(0), 0) % INTERSTITIAL.length;
  return (
    <div className="syo-quiz__pad syo-calc" role="status">
      <div>
        <div className="syo-calc__dots" aria-hidden="true"><span /><span /><span /></div>
        <div className="syo-kicker">{t(INTERSTITIAL[idx])}</div>
      </div>
    </div>
  );
}

function Quiz({ quiz, result, onAskAi, t, lang }) {
  const rootRef = useRef(null);
  const firstRender = useRef(true);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const root = rootRef.current;
    if (!root) return;
    if (typeof quiz.step === 'number') {
      const opt = root.querySelector('.syo-opt[aria-checked="true"]') || root.querySelector('[data-autofocus="true"]');
      if (opt) opt.focus({ preventScroll: true });
      scrollIntoViewIfNeeded(root);
    } else if (quiz.step === 'result') {
      const target = root.querySelector('[data-result-focus]') || root;
      target.focus({ preventScroll: true });
      scrollIntoViewIfNeeded(root);
    } else if (quiz.step === 'intro') {
      const btn = root.querySelector('.syo-btn');
      if (btn) btn.focus({ preventScroll: true });
    }
  }, [quiz.step]);

  let body;
  if (quiz.step === 'intro') body = <Intro quiz={quiz} onAskAi={onAskAi} t={t} />;
  else if (quiz.step === 'calc') body = <Calculating answers={quiz.answers} t={t} />;
  else if (quiz.step === 'result' && result) {
    body = (
      <div className="syo-quiz__pad">
        <ResultView answers={quiz.answers} result={result} shared={quiz.shared} onRetake={quiz.retake} onAskAi={onAskAi} t={t} lang={lang} />
      </div>
    );
  } else if (typeof quiz.step === 'number') body = <QuestionCard index={quiz.step} quiz={quiz} t={t} />;
  else body = <Intro quiz={quiz} onAskAi={onAskAi} t={t} />;

  return (
    <div className="syo-quiz" ref={rootRef} tabIndex={-1} role="region" aria-label={t({ zh: 'OpenViking 适配测试', en: 'OpenViking fit test' })}>
      {body}
    </div>
  );
}

/* ---------- prompt box ---------- */

function PromptBox({ quiz, result, t, lang, boxRef }) {
  // A shared result belongs to someone else; don't put their answers in "my" prompt.
  const complete = Boolean(result) && !quiz.shared;
  const [tab, setTab] = useState(null);
  const active = tab || (complete ? 'answers' : 'interview');
  const text = active === 'answers' && complete
    ? promptWithAnswers(quiz.answers, result, lang)
    : promptInterview(lang);
  const tabs = [
    { key: 'interview', label: { zh: '让 AI 来问我', en: 'Let the AI interview me' } },
    { key: 'answers', label: { zh: '带上我的测验结果', en: 'With my quiz result' } },
  ];
  return (
    <div className="syo-prompt" ref={boxRef}>
      <div className="syo-tabs" role="tablist">
        {tabs.map(x => {
          const disabled = x.key === 'answers' && !complete;
          return (
            <button
              key={x.key}
              id={`syo-tab-${x.key}`}
              type="button"
              role="tab"
              className={`syo-tab ${disabled ? 'is-pending' : ''}`}
              aria-selected={active === x.key}
              aria-controls="syo-tabpanel"
              tabIndex={active === x.key ? 0 : -1}
              onClick={() => setTab(x.key)}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                const nextKey = x.key === 'interview' ? 'answers' : 'interview';
                setTab(nextKey);
                document.getElementById(`syo-tab-${nextKey}`)?.focus();
              }}
            >{t(x.label)}</button>
          );
        })}
      </div>
      <div className="syo-prompt__body" role="tabpanel" id="syo-tabpanel" aria-labelledby={`syo-tab-${active}`}>
        {active === 'answers' && !complete ? (
          <p className="syo-prompt__intro">{t({
            zh: '先做完上面的测验，这段 prompt 会带上你的 12 个答案和测验结果，自动填好。',
            en: 'Finish the quiz above and this prompt fills itself in with your twelve answers and your result.',
          })}</p>
        ) : (
          <>
            <p className="syo-prompt__intro">{active === 'answers' ? t({
              zh: '包含你的答案、测验结果和 OpenViking 的公开事实。AI 会给出自己的适配度、方案建议，以及它和测验结论的分歧。',
              en: 'Includes your answers, the quiz result and the public facts about OpenViking. The AI returns its own fit score, an edition verdict and where it disagrees with the quiz.',
            }) : t({
              zh: '不用先答题。AI 会先看你的项目（只读，能看到的话），再一题一题问你，最后给出适配度和方案建议。',
              en: "No quiz needed. The AI looks at your project first (read-only, if it can see one), then asks you questions one at a time, and ends with a fit score and an edition verdict.",
            })}</p>
            <pre className="syo-pre">{text}</pre>
            <div className="syo-prompt__foot">
              <CopyButton t={t} ghost={false} event={`prompt-${active}`} label={{ zh: '复制 prompt', en: 'Copy prompt' }} getText={() => text} />
              <span className="syo-note">{t({ zh: '最好交给能读代码的 coding agent：Claude Code、Codex、Cursor 都行。', en: 'Works best in a coding agent that can read your repo: Claude Code, Codex or Cursor.' })}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------- static sections ---------- */

function FieldGuide({ result, shared, t }) {
  return (
    <div className="syo-guide">
      {PERSONA_ORDER.map(k => {
        const p = PERSONAS[k];
        const you = result && result.persona === k;
        return (
          <div key={k} className={`syo-mini ${you ? 'is-you' : ''}`}>
            <div className="syo-mini__top">
              <span className="syo-kicker">No. {p.no}</span>
              {you ? <span className="syo-kicker syo-you">{t(shared ? { zh: 'TA 在这里', en: 'Their type' } : { zh: '你在这里', en: 'You are here' })}</span> : null}
            </div>
            <div className="syo-mini__name">{t(p.name)}</div>
            <p className="syo-mini__motto">{t(p.motto)}</p>
          </div>
        );
      })}
    </div>
  );
}

const ED_ROWS = [
  ['who', { zh: '适合谁', en: 'For' }],
  ['ops', { zh: '运维', en: 'Ops' }],
  ['models', { zh: '模型', en: 'Models' }],
  ['data', { zh: '数据在哪', en: 'Data lives' }],
  ['cost', { zh: '费用', en: 'Cost' }],
];

function EditionCards({ result, shared, t }) {
  return (
    <div className="syo-editions">
      {FORM_ORDER.map(k => {
        const f = FORMS[k];
        const pick = result && !result.lowFit && result.forms.top === k;
        return (
          <div key={k} className={`syo-ed ${pick ? 'is-pick' : ''}`}>
            <div className="syo-kicker">{pick ? <span className="syo-you">{t(shared ? { zh: 'TA 的推荐方案', en: 'Their pick' } : { zh: '你的推荐方案', en: 'Recommended for you' })}</span> : t(f.sub)}</div>
            <div className="syo-ed__name">{t(f.name)}</div>
            <p className="syo-ed__tag">{t(f.tagline)}</p>
            <dl>
              {ED_ROWS.map(([key, label]) => (
                <div key={key}>
                  <dt>{t(label)}</dt>
                  <dd>{t(f[key])}</dd>
                </div>
              ))}
            </dl>
            <div className="syo-ed__links" {...linkTracker(`editions/${k}`, f.cta, t)}>
              {f.cta.map(c => <Ext key={t(c.label)} href={c.href}>{t(c.label)}</Ext>)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- post ---------- */

function ShouldYouUseOpenViking() {
  const { t, lang, navigate } = useBlog();
  const quiz = useQuiz(navigate);
  const complete = isComplete(quiz.answers);
  const result = useMemo(() => (complete ? evaluate(quiz.answers) : null), [complete, quiz.answers]);
  const shownResult = quiz.step === 'result' ? result : null;
  const promptRef = useRef(null);

  const askAi = useCallback(() => {
    const el = promptRef.current;
    if (!el) return;
    const y = el.getBoundingClientRect().top + window.scrollY - 96;
    window.scrollTo({ top: y, behavior: reducedMotion() ? 'auto' : 'smooth' });
  }, []);

  return (
    <Article className="syo">
      <Styles />

      <Lead>{t({
        zh: '你的 agent 很聪明，就是记性不太好：昨天刚纠正过的约定，今天又得讲一遍；AGENTS.md 越写越长，长到 agent 自己都读不完。OpenViking 就是为这类问题做的上下文数据库。它能帮你多少，做完下面的测试就知道。',
        en: 'Your agent is brilliant and has the memory of a goldfish. The convention you corrected yesterday needs correcting again today, and your AGENTS.md is now longer than the agent is willing to read. OpenViking is a context database built for exactly this. The test below shows how much it can help you.',
      })}</Lead>

      <P>{t({
        zh: '下面 12 道题，大约三分钟。做完你会拿到一张饲养员类型卡、一份五维画像和适配度分数，还会知道开源版、托管版和私有化部署版里哪个方案最适合你。不想点选项，也可以把我们写好的 prompt 交给你自己的 AI，让它来问你。',
        en: "Twelve questions, about three minutes. You'll get a wrangler-type card, a five-axis profile with a fit score, and a straight answer on whether open source, the managed service or a private deployment suits you best. Rather not click through? Hand our prompt to your own AI and let it interview you instead.",
      })}</P>

      <H2 id="take-the-test">{t({ zh: '开始测试', en: 'Take the test' })}</H2>

      <Quiz quiz={quiz} result={shownResult} onAskAi={askAi} t={t} lang={lang} />

      <H2 id="ask-your-ai">{t({ zh: '让你自己的 AI 来判断', en: 'Let your own AI weigh in' })}</H2>

      <P>{t({
        zh: '测验按固定规则计分，只看你的选项。想让判断结合你的真实项目，可以把下面两段 prompt 交给你常用的 AI，最好是能读代码仓库的 coding agent。',
        en: 'The quiz scores your answers with fixed rules. To bring your actual project into the verdict, hand either prompt below to the AI you use, ideally a coding agent that can read your repository.',
      })}</P>
      <Ul>
        <Li>{t({
          zh: <><strong>让 AI 来问我</strong>：不用先答题，AI 会先看项目，再一题题问你。</>,
          en: <><strong>Let the AI interview me</strong>: no quiz needed. The AI looks at your project, then asks you questions one at a time.</>,
        })}</Li>
        <Li>{t({
          zh: <><strong>带上我的测验结果</strong>：做完测验后自动填好，适合拿结果去问第二意见。</>,
          en: <><strong>With my quiz result</strong>: fills itself in once you finish the quiz. Use it for a second opinion.</>,
        })}</Li>
      </Ul>

      <PromptBox quiz={quiz} result={shownResult} t={t} lang={lang} boxRef={promptRef} />

      <H2 id="field-guide">{t({ zh: '十种 Agent 饲养员', en: 'Ten kinds of agent wrangler' })}</H2>

      <P>{t({
        zh: '测验结果会落在下面其中一种。做完测验，你的那一种会被标出来。',
        en: 'Every result lands on one of these. Once you finish the quiz, yours is marked.',
      })}</P>

      <FieldGuide result={shownResult} shared={quiz.shared} t={t} />

      <H2 id="editions">{t({ zh: '开源、托管、私有化，怎么选', en: 'Open source, managed or private' })}</H2>

      <P>{t({
        zh: '开源版和托管版共享同一套代码内核。几种方案的区别在于谁来部署和运维、数据放在哪里、怎么付费。托管版运行在火山引擎上，分个人版和企业版两档。',
        en: 'Open source and the managed service share one code core. The editions differ in who deploys and operates it, where the data lives, and how you pay. The managed service runs on Volcengine in two plans, Personal and Enterprise.',
      })}</P>

      <EditionCards result={shownResult} shared={quiz.shared} t={t} />

      <p className="syo-note">{t({
        zh: <>托管版最新价格见火山引擎<A href={LINKS.billing}>计费说明</A>。</>,
        en: <>For current managed-service prices, see the Volcengine <A href={LINKS.billing}>pricing page</A>.</>,
      })}</p>

      <H3>{t({ zh: '什么时候你不需要它', en: "When you don't need it" })}</H3>

      <Ul>
        <Li>{t({
          zh: '现有的向量检索或 RAG 已经够用：先继续用就好。',
          en: 'Your current vector search or RAG already works well: keep using it.',
        })}</Li>
        <Li>{t({
          zh: '应用是无状态的，或者每次都是一次性调用：没有什么需要记住的。',
          en: 'Your app is stateless or every call is one-shot: there is nothing to remember.',
        })}</Li>
        <Li>{t({
          zh: '要求刚说完的话，下一秒就能从长期记忆里召回：OpenViking 的记忆在会话提交后异步提取，这部分要靠会话内的上下文。',
          en: 'You need what was just said to be recallable from long-term memory a second later: OpenViking extracts memories asynchronously after a session is committed, so that part belongs in the session context.',
        })}</Li>
      </Ul>

      <P>{t({
        zh: <>前两种情况，测验会主动压低分数；第一条也是官方 <A href={t(LINKS.faq)}>FAQ</A> 的建议。</>,
        en: <>The quiz deliberately lowers the score for the first two; the first is also what the official <A href={t(LINKS.faq)}>FAQ</A> advises.</>,
      })}</P>

      <H2 id="scoring">{t({ zh: '计分规则', en: 'How the scoring works' })}</H2>

      <P>{t({
        zh: '适配度主要看两件事：agent 有多健忘，要用的资料有多少、有多散。协作规模和管控要求再加一点分。任务都是一次性的，或者现有的向量检索已经够用，分数会被压低；低于 30 分，结论就是暂时不需要。',
        en: 'Fit comes mostly from two things: how forgetful your agents are, and how much material they need and how scattered it is. Team size and control requirements add a little on top. One-shot tasks, or vector search that already works, pull the score down; below 30, the verdict is that you don’t need it yet.',
      })}</P>

      <P>{t({
        zh: '地域、运维、模型来源和预算不影响适配度，只决定推荐哪种方案。数据必须留在自己的云账号或机房，或者外部模型 API 一律不许用时，推荐会在开源版和私有化部署版之间选；多人共用或者管着一群 agent 时，托管版推荐企业版。',
        en: 'Region, ops, model source and budget never touch the fit score; they only decide which edition we recommend. If data must stay in your own cloud account or data center, or external model APIs are banned, the pick comes from Open source or Private deployment; if several people share the context or you run a fleet of agents, the managed pick is Managed · Enterprise.',
      })}</P>

      <P>{t({
        zh: '前两名相差不到 5 分时，开源版和托管版之间优先推荐托管版：省下的部署和运维时间，通常比账单更值钱，除非运维本来就是你的爱好；私有化部署版和其他方案之间，有 SRE 或平台团队的优先私有化。预算选了“不花钱”时，开源版始终排在企业版和私有化部署版前面。',
        en: 'When the top two are less than 5 points apart, a managed plan beats Open source, because the setup and ops time you save is usually worth more than the bill, unless running infrastructure is your idea of fun; Private deployment beats the other option when you have SREs or a platform team. If you answered that you wouldn’t pay, Open source always ranks above Managed · Enterprise and Private deployment.',
      })}</P>

      <H2 id="next-steps">{t({ zh: '下一步', en: 'Next steps' })}</H2>

      <P>{t({
        zh: '不管测出来是哪种饲养员，最快的验证方式都是拿一个真实项目试两周。',
        en: 'Whatever kind of wrangler you turned out to be, the fastest test is two weeks on a real project.',
      })}</P>

      <Ul>
        <Li>{t({
          zh: <>想自己动手：看<A href={t(LINKS.quickstart)}>快速开始</A>，或者把<A href={t(LINKS.agentSetup)}>这份安装说明</A>直接交给你的 agent。自己装的话，先跑 <InlineCode>uv tool install openviking --upgrade && openviking-server init</InlineCode>，向导会帮你配好模型。</>,
          en: <>Want to run it yourself: read the <A href={t(LINKS.quickstart)}>quickstart</A>, or hand <A href={t(LINKS.agentSetup)}>the setup guide</A> straight to your agent. By hand, start with <InlineCode>uv tool install openviking --upgrade && openviking-server init</InlineCode>; the wizard sets up your models.</>,
        })}</Li>
        <Li>{t({
          zh: <>不想运维：<A href={LINKS.hostedQuickstart}>开通托管版</A>，个人版每个库前 50 个文件免费；团队直接看<A href={LINKS.hostedProduct}>企业版</A>。</>,
          en: <>Rather not run servers: <A href={LINKS.hostedQuickstart}>start the managed service</A>, where each Personal library's first 50 files are free; teams can go straight to <A href={LINKS.hostedProduct}>Enterprise</A>.</>,
        })}</Li>
        <Li>{t({
          zh: <>数据不能出门：<A href={t(LINKS.privateForm)}>申请私有化试用</A>，团队确认后会把安装包和试用 License 发到你的邮箱。</>,
          en: <>Data can't leave the building: <A href={t(LINKS.privateForm)}>request a private-deployment trial</A>; once the team confirms, the package and a trial license arrive by email.</>,
        })}</Li>
        <Li>{t({
          zh: <>还拿不准：来 <A href={LINKS.github}>GitHub</A> 或 <A href={LINKS.discord}>Discord</A> 问我们，也可以直接问页面角落里的 VikingBot，文档它都读过。</>,
          en: <>Still unsure: ask us on <A href={LINKS.github}>GitHub</A> or <A href={LINKS.discord}>Discord</A>, or ask VikingBot in the corner of this page. It has read the docs so you don't have to.</>,
        })}</Li>
      </Ul>
    </Article>
  );
}

export default {
  id: SLUG,
  Component: ShouldYouUseOpenViking,
  meta: {
    title: {
      zh: '你该用 OpenViking 吗？三分钟测出你是哪种 Agent 饲养员',
      en: 'Should You Use OpenViking? A Three-Minute Test for Agent Wranglers',
    },
    description: {
      zh: '12 道题，测出你的 Agent 饲养员类型、五维画像和 OpenViking 适配度，再告诉你开源版、托管版和私有化部署版哪种更合适。不想答题，也可以把 prompt 交给你自己的 AI 来判断。',
      en: 'Twelve questions reveal your agent-wrangler type, a five-axis profile and an OpenViking fit score, then tell you whether open source, the managed service or a private deployment fits best. Or hand a prompt to your own AI and let it judge.',
    },
    cover: COVER,
    cardCover: CARD_COVER,
    publishedAt: '2026-10-03',
    readingTime: { en: 5, zh: 5 },
    category: { zh: '实践', en: 'Field Notes' },
    tags: ['openviking', 'memory', 'agent-memory', 'context', 'agents'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'tosaki', github: 't0saki', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
