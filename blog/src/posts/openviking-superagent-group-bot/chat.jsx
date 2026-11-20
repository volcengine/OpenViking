import React, { useId } from 'react';
import { A, useBlog } from '../../blog-components';

/* Chat transcript figure, ported from the Astro ```chat renderer.
 *
 * <ChatStyles /> must be rendered once per page (the stylesheet is shared by
 * every <Chat />). <Chat chat={{ zh, en }} /> picks the active language.
 * Data shape: see the header of chats.js.
 *
 * Text rules (same as the original):
 * - caption: [text](href) links only, everything else literal;
 * - message text: blank line = paragraph, newline = <br>, `code` spans,
 *   @Name of a known person highlighted, █ runs drawn as redaction bars.
 *   Nothing else is interpreted, so **stars** stay as typed;
 * - every other field: literal text plus redaction bars.
 */

const P = 'ovc';

// ------------------------------------------------------------------ text

const REDACTED = /█+/g;

function redacted(text, keyBase = 'r') {
  const out = [];
  let last = 0;
  let i = 0;
  for (const match of String(text).matchAll(REDACTED)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(<span key={`${keyBase}-${i++}`} className={`${P}__redacted`}>{match[0]}</span>);
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function mentionPattern(names) {
  const sorted = [...new Set(names)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (sorted.length === 0) return undefined;
  const alternatives = sorted.map(
    (name) => escapeRegExp(name) + (/[A-Za-z0-9_]$/.test(name) ? '(?![A-Za-z0-9_])' : ''),
  );
  return new RegExp(`@(?:${alternatives.join('|')})`, 'g');
}

function decorateLine(line, mentions, keyBase) {
  if (!mentions) return redacted(line, keyBase);
  const out = [];
  let last = 0;
  let i = 0;
  for (const match of line.matchAll(mentions)) {
    if (match.index > last) out.push(...redacted(line.slice(last, match.index), `${keyBase}-t${i}`));
    out.push(
      <span key={`${keyBase}-at${i}`} className={`${P}__at`}>
        {redacted(match[0], `${keyBase}-a${i}`)}
      </span>,
    );
    i += 1;
    last = match.index + match[0].length;
  }
  if (last < line.length) out.push(...redacted(line.slice(last), `${keyBase}-end`));
  return out;
}

const CODE_SPAN = /`([^`\n]+)`/g;

function messageLine(line, mentions, keyBase) {
  const out = [];
  let last = 0;
  let i = 0;
  for (const match of line.matchAll(CODE_SPAN)) {
    if (match.index > last) out.push(...decorateLine(line.slice(last, match.index), mentions, `${keyBase}-l${i}`));
    out.push(
      <code key={`${keyBase}-c${i}`} className={`${P}__code`}>
        {redacted(match[1], `${keyBase}-cr${i}`)}
      </code>,
    );
    i += 1;
    last = match.index + match[0].length;
  }
  if (last < line.length) out.push(...decorateLine(line.slice(last), mentions, `${keyBase}-le`));
  return out;
}

function messageText(text, mentions) {
  const body = text.replace(/^(?:[ \t]*\n)+/, '').trimEnd();
  return body.split(/\n[ \t]*\n\s*/).map((paragraph, p) => (
    <div key={p} className={`${P}__p`}>
      {paragraph.split('\n').map((line, l) => (
        <React.Fragment key={l}>
          {l > 0 ? <br /> : null}
          {messageLine(line, mentions, `p${p}-${l}`)}
        </React.Fragment>
      ))}
    </div>
  ));
}

const CAPTION_LINK = /\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g;

function captionParts(value) {
  const out = [];
  let last = 0;
  let i = 0;
  for (const match of value.matchAll(CAPTION_LINK)) {
    const [whole, label, href] = match;
    if (match.index > last) out.push(...redacted(value.slice(last, match.index), `c${i}`));
    out.push(<A key={`link${i}`} href={href}>{redacted(label, `cl${i}`)}</A>);
    i += 1;
    last = match.index + whole.length;
  }
  if (last < value.length) out.push(...redacted(value.slice(last), 'cend'));
  return out;
}

// ------------------------------------------------------------------ people

function firstGrapheme(name) {
  const trimmed = name.trim();
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    for (const { segment } of new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(trimmed)) {
      return segment;
    }
  }
  return Array.from(trimmed)[0] ?? name;
}

const COLORS = [1, 2, 3, 4, 5, 6];
const NO_SETTINGS = {};

/* Explicit colour, else the next free slot in order of first appearance.
   Self and bots (accent hue) take no slot unless given one. */
function dealColors(people, messages) {
  const taken = new Set(Object.values(people).map((p) => p.color).filter(Boolean));
  const free = COLORS.filter((c) => !taken.has(c));
  const pool = free.length > 0 ? free : COLORS;
  const colors = new Map();
  let dealt = 0;
  for (const entry of messages) {
    if (!entry.from || colors.has(entry.from)) continue;
    const person = people[entry.from] ?? NO_SETTINGS;
    if (person.color !== undefined) colors.set(entry.from, person.color);
    else if (!person.self && !person.bot) colors.set(entry.from, pool[dealt++ % pool.length]);
  }
  return colors;
}

// ------------------------------------------------------------------ pieces

const rowKind = (row) => Object.keys(row)[0];

function CardRow({ row }) {
  const kind = rowKind(row);
  const value = row[kind];
  switch (kind) {
    case 'title':
      return <div className={`${P}__card-title`}>{redacted(value)}</div>;
    case 'heading':
      return <div className={`${P}__card-h`}>{redacted(value)}</div>;
    case 'text':
      return <div className={`${P}__card-t`}>{redacted(value)}</div>;
    case 'lines':
    case 'bullets':
      return (
        <div className={`${P}__bars${kind === 'bullets' ? ' is-bullets' : ''}`} aria-hidden="true">
          {value.map((w, i) => <span key={i} style={{ width: `${w}%` }} />)}
        </div>
      );
    case 'rule':
      return <div className={`${P}__card-hr`} aria-hidden="true" />;
    case 'stats':
      return (
        <div className={`${P}__card-stats`}>
          {Object.entries(value).map(([label, v]) => (
            <div key={label}>
              <span className={`${P}__card-k`}>{redacted(label)}</span>{' '}
              <span className={`${P}__card-v`}>{redacted(String(v))}</span>
            </div>
          ))}
        </div>
      );
    case 'tiles':
      return (
        <div className={`${P}__card-grid`} aria-hidden="true">
          {Array.from({ length: value }, (_, i) => <span key={i} />)}
        </div>
      );
    case 'plot':
      return (
        <div className={`${P}__card-plot`} aria-hidden="true">
          {value.map(([x, y], i) => <span key={i} style={{ left: `${x}%`, top: `${y}%` }} />)}
        </div>
      );
    default:
      return null;
  }
}

function Card({ card }) {
  return (
    <div className={`${P}__card${card.inline ? ' is-inline' : ''}`}>
      {card.label ? <div className={`${P}__card-label`}>{redacted(card.label)}</div> : null}
      {card.rows.map((row, i) => <CardRow key={i} row={row} />)}
    </div>
  );
}

const Sep = ({ children = ': ' }) => <span className={`${P}__sep`}>{children}</span>;

function Message({ entry, person, color, continued, mentions }) {
  const cls = [`${P}__msg`];
  if (person.self) cls.push('is-self');
  if (person.bot) cls.push('is-bot');
  if (continued) cls.push('is-cont');

  const hue = person.bot && person.color === undefined ? 'bot' : color;
  const showBadge = person.badge ?? !!person.bot;
  const inlineCard = entry.card?.inline ? entry.card : undefined;
  const hasText = typeof entry.text === 'string' && entry.text.trim() !== '';
  const reactions = entry.reactions ?? [];

  return (
    <div className={cls.join(' ')}>
      {person.self ? null : (
        <span className={`${P}__av ${P}__av--${hue}`} aria-hidden="true">
          {person.avatar ?? firstGrapheme(entry.from)}
        </span>
      )}
      <div className={`${P}__body`}>
        <div className={`${P}__who${person.self || continued ? ` ${P}__vh` : ''}`}>
          {redacted(entry.from)}
          {showBadge ? <>{' '}<span className={`${P}__badge`}>Bot</span></> : null}
        </div>
        <Sep />
        {hasText || entry.reply || inlineCard ? (
          <div className={`${P}__bubble`}>
            {entry.reply ? (
              <div className={`${P}__quote`}>
                <span className={`${P}__quote-who`}>{redacted(entry.reply.from)}</span>
                <Sep />
                <span className={`${P}__quote-text`}>{redacted(entry.reply.text)}</span>
              </div>
            ) : null}
            {hasText ? messageText(entry.text, mentions) : null}
            {inlineCard ? <Card card={inlineCard} /> : null}
          </div>
        ) : null}
        {entry.voice !== undefined ? (
          <div className={`${P}__bubble ${P}__voice`}>
            <span className={`${P}__wave`} aria-hidden="true">
              {[0, 1, 2, 3, 4].map((i) => <span key={i} />)}
            </span>
            {`${entry.voice}″`}
          </div>
        ) : null}
        {entry.image ? (
          <div className={`${P}__media`}>
            <img src={entry.image.src} alt={entry.image.alt}
                 width={entry.image.width} height={entry.image.height}
                 loading="lazy" decoding="async" />
          </div>
        ) : null}
        {entry.card && !inlineCard ? <Card card={entry.card} /> : null}
        {reactions.length > 0 ? (
          <div className={`${P}__reacts`}>
            {reactions.map((r, i) => <span key={i} className={`${P}__react`}>{redacted(r)}</span>)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ block

export function Chat({ chat }) {
  const { lang } = useBlog();
  const id = useId();
  const data = chat[lang] ?? chat.en ?? chat.zh;
  const people = data.people ?? {};
  const messages = data.messages ?? [];

  const speakers = [];
  const names = new Set(Object.keys(people));
  for (const entry of messages) {
    if (!entry.from) continue;
    if (!speakers.includes(entry.from)) speakers.push(entry.from);
    names.add(entry.from);
    if (entry.reply) names.add(entry.reply.from);
  }
  const mentions = mentionPattern(names);
  const colors = dealColors(people, messages);

  const headId = `${id}-head`;
  const captionId = `${id}-cap`;
  const scroll = data.maxHeight !== 'none';

  const feedProps = { className: `${P}__feed` };
  if (scroll) {
    feedProps.className += ' is-scroll';
    feedProps.tabIndex = 0;
    feedProps.role = 'region';
    if (data.caption) feedProps['aria-labelledby'] = captionId;
    else if (data.title || data.meta) feedProps['aria-labelledby'] = headId;
    else feedProps['aria-label'] = speakers.join(', ');
  }

  let previous;
  const feed = messages.map((entry, i) => {
    for (const marker of ['time', 'note', 'gap']) {
      if (!entry.from && entry[marker] !== undefined) {
        previous = undefined;
        return <div key={i} className={`${P}__${marker}`}>{redacted(String(entry[marker]))}</div>;
      }
    }
    const node = (
      <Message key={i} entry={entry} person={people[entry.from] ?? NO_SETTINGS}
               color={colors.get(entry.from)} continued={previous === entry.from}
               mentions={mentions} />
    );
    previous = entry.from;
    return node;
  });

  const style = data.maxHeight && data.maxHeight !== 'none' ? { '--ovc-max-h': data.maxHeight } : undefined;

  return (
    <figure className={`${P}${data.wide ? ` ${P}--wide` : ''}`} style={style}>
      <div className={`${P}__log`}>
        {data.title || data.meta ? (
          <div className={`${P}__head`} id={headId}>
            {data.title ? <span className={`${P}__title`}>{redacted(data.title)}</span> : null}
            {data.title && data.meta ? <Sep>{' · '}</Sep> : null}
            {data.meta ? <span className={`${P}__meta`}>{redacted(data.meta)}</span> : null}
          </div>
        ) : null}
        <div {...feedProps}>{feed}</div>
      </div>
      {data.caption ? (
        <figcaption className={`${P}__cap`} id={captionId}>{captionParts(data.caption)}</figcaption>
      ) : null}
    </figure>
  );
}

// ------------------------------------------------------------------ styles

const CSS = `
.ovc {
  --ovc-fg: var(--th-ink);
  --ovc-bg: var(--th-bg);
  --ovc-surface: var(--th-bg-2);
  --ovc-mfg: color-mix(in oklab, var(--th-mute) 78%, var(--th-ink));
  --ovc-accent: var(--th-accent);
  --ovc-frame: var(--th-line);
  --ovc-line: color-mix(in oklab, var(--ovc-fg) 13%, var(--ovc-surface));
  --ovc-self: color-mix(in oklab, var(--ovc-accent) 11%, var(--ovc-bg));
  --ovc-skel: color-mix(in oklab, var(--ovc-fg) 12%, transparent);
  --ovc-av-mix: 26%;
  --ovc-shadow: color-mix(in oklab, var(--th-mute) 30%, transparent);
  --ovc-focus: color-mix(in oklab, var(--ovc-fg) 70%, transparent);
  --ovc-max-h: min(28rem, 55svh);
  display: block;
  box-sizing: border-box;
  width: min(100%, 25rem);
  max-width: 100%;
  margin: 2rem auto;
  padding: 0;
  border: 0;
  color: var(--ovc-fg);
  font-family: var(--th-font-body);
  font-size: 0.9375rem;
  font-weight: 400;
  line-height: 1.6;
  text-align: start;
}
.ovc.ovc--wide { width: min(100%, 40rem); }
@supports not (height: 1svh) { .ovc { --ovc-max-h: min(28rem, 55vh); } }

.ovc *, .ovc *::before, .ovc *::after { box-sizing: border-box; }
.ovc :where(div, span) { margin: 0; padding: 0; border: 0; font-style: normal; }
.ovc :where(img) {
  display: block; max-width: 100%; height: auto;
  margin: 0; padding: 0; border: 0; box-shadow: none;
}
.ovc :where(code) {
  font-family: var(--th-font-mono);
  font-size: 0.875em;
  font-weight: 400;
  color: inherit;
}
.ovc__cap .b-a { overflow-wrap: anywhere; }

.ovc__log {
  --ovc-px: 0.75rem;
  overflow: hidden;
  overflow: clip;
  border: 1px solid var(--ovc-frame);
  border-radius: 0.85rem;
  background: var(--ovc-surface);
}
.ovc__head {
  display: flex;
  flex-wrap: wrap;
  gap: 0 0.75rem;
  align-items: baseline;
  justify-content: space-between;
  margin: 0 var(--ovc-px);
  padding: 0.75rem 0.1rem 0.5rem;
  border-bottom: 1px solid var(--ovc-line);
}
.ovc__title { font-size: 0.9375rem; font-weight: 600; }
.ovc__meta { margin-inline-start: auto; color: var(--ovc-mfg); font-size: 0.875rem; }
.ovc__cap {
  display: block;
  margin: 0.65rem 0 0;
  padding: 0 0.25rem;
  color: var(--ovc-mfg);
  font-size: 0.875rem;
  line-height: 1.6;
  text-align: center;
}

.ovc__feed {
  --ovc-pt: 0.35rem;
  --ovc-pb: 0.9rem;
  position: relative;
  padding: var(--ovc-pt) var(--ovc-px) var(--ovc-pb);
}
.ovc__feed.is-scroll {
  --ovc-thumb: color-mix(in oklab, var(--ovc-fg) 20%, transparent);
  max-height: var(--ovc-max-h);
  overflow-x: hidden;
  overflow-y: auto;
  background:
    linear-gradient(var(--ovc-surface) 55%, transparent) top / 100% 3.5rem no-repeat local,
    linear-gradient(transparent, var(--ovc-surface) 45%) bottom / 100% 3.5rem no-repeat local,
    linear-gradient(var(--ovc-shadow), transparent) top / 100% 1.75rem no-repeat scroll,
    linear-gradient(transparent, var(--ovc-shadow)) bottom / 100% 1.75rem no-repeat scroll;
  background-color: var(--ovc-surface);
  scrollbar-color: var(--ovc-thumb) var(--ovc-surface);
  scrollbar-width: thin;
}
.ovc__feed.is-scroll:hover { --ovc-thumb: color-mix(in oklab, var(--ovc-fg) 32%, transparent); }

@supports (animation-timeline: scroll()) {
  .ovc__feed.is-scroll {
    --ovc-fade: 4rem;
    --ovc-fade-range: min(var(--ovc-fade), 50%);
    background: var(--ovc-surface);
  }
  .ovc__feed.is-scroll::before,
  .ovc__feed.is-scroll::after {
    content: "";
    position: sticky;
    z-index: 3;
    display: block;
    height: var(--ovc-fade);
    margin-inline: calc(-1 * var(--ovc-px));
    opacity: 0;
    pointer-events: none;
    animation: ovc-fade-in linear both;
    animation-timeline: scroll();
  }
  .ovc__feed.is-scroll::before {
    top: calc(-1 * var(--ovc-pt));
    margin-bottom: calc(-1 * var(--ovc-fade));
    background: linear-gradient(to bottom,
      var(--ovc-surface) 0%, var(--ovc-surface) 18%,
      color-mix(in oklab, var(--ovc-surface) 82%, transparent) 38%,
      color-mix(in oklab, var(--ovc-surface) 50%, transparent) 62%,
      color-mix(in oklab, var(--ovc-surface) 18%, transparent) 84%,
      transparent 100%);
    animation-range: 0 var(--ovc-fade-range);
  }
  .ovc__feed.is-scroll::after {
    bottom: calc(-1 * var(--ovc-pb));
    margin-top: calc(-1 * var(--ovc-fade));
    background: linear-gradient(to top,
      var(--ovc-surface) 0%, var(--ovc-surface) 18%,
      color-mix(in oklab, var(--ovc-surface) 82%, transparent) 38%,
      color-mix(in oklab, var(--ovc-surface) 50%, transparent) 62%,
      color-mix(in oklab, var(--ovc-surface) 18%, transparent) 84%,
      transparent 100%);
    animation-name: ovc-fade-out;
    animation-range: calc(100% - var(--ovc-fade-range)) 100%;
  }
}
@keyframes ovc-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes ovc-fade-out { from { opacity: 1; } to { opacity: 0; } }

.ovc__feed.is-scroll:focus-visible {
  border-radius: 0.6rem;
  outline: 2px solid var(--ovc-focus);
  outline-offset: -2px;
}
@supports selector(:has(*)) {
  .ovc__feed.is-scroll:focus-visible { outline: 0; }
  .ovc__log:has(> .ovc__feed:focus-visible) {
    outline: 2px solid var(--ovc-focus);
    outline-offset: 3px;
  }
}

@media print {
  .ovc__head, .ovc__msg, .ovc__time, .ovc__note, .ovc__gap, .ovc__card { break-inside: avoid; }
  .ovc__log { overflow: visible; }
  .ovc__feed.is-scroll { max-height: none; overflow: visible; background: none; }
  .ovc__feed.is-scroll::before, .ovc__feed.is-scroll::after { content: none; }
}

.ovc__sep, .ovc__vh {
  position: absolute !important;
  width: 1px; height: 1px;
  margin: -1px; padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  border: 0;
  white-space: nowrap;
}

.ovc__time, .ovc__note, .ovc__gap {
  margin: 0.7rem auto 0.2rem;
  color: var(--ovc-mfg);
  font-size: 0.875rem;
  line-height: 1.5;
  text-align: center;
}
.ovc__note {
  width: fit-content;
  max-width: 100%;
  padding: 0.1rem 0.6rem;
  border-radius: 999px;
  background: color-mix(in oklab, var(--ovc-fg) 6%, transparent);
}
.ovc__gap { display: flex; gap: 0.75rem; align-items: center; }
.ovc__gap::before, .ovc__gap::after {
  content: "";
  flex: 1 1 0;
  min-width: 1.5rem;
  border-top: 1px dashed color-mix(in oklab, var(--ovc-mfg) 55%, var(--ovc-surface));
}

.ovc__msg {
  position: relative;
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  margin-top: 0.7rem;
}
.ovc__msg.is-cont { margin-top: 0.3rem; }
.ovc__msg.is-self { flex-direction: row-reverse; }

.ovc__av {
  --ovc-hue: var(--ovc-mfg);
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  background: color-mix(in oklab, var(--ovc-hue) var(--ovc-av-mix), var(--ovc-bg));
  color: color-mix(in oklab, var(--ovc-hue) 62%, var(--ovc-fg));
  font-size: 0.875rem;
  font-weight: 600;
  line-height: 1;
  user-select: none;
}
.ovc__msg.is-cont .ovc__av { height: 0; visibility: hidden; }
/* Six mid-lightness hues, readable as a tint on light and dark grounds; all
   kept away from the blue accent that marks bots. */
.ovc__av--1 { --ovc-hue: oklch(0.64 0.13 30); }
.ovc__av--2 { --ovc-hue: oklch(0.66 0.12 75); }
.ovc__av--3 { --ovc-hue: oklch(0.64 0.12 140); }
.ovc__av--4 { --ovc-hue: oklch(0.64 0.10 190); }
.ovc__av--5 { --ovc-hue: oklch(0.62 0.13 300); }
.ovc__av--6 { --ovc-hue: oklch(0.64 0.13 350); }
.ovc__av--bot { --ovc-hue: var(--ovc-accent); }

.ovc__body {
  display: flex;
  flex: 0 1 auto;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  max-width: calc(100% - 3.5rem);
}
.ovc__msg.is-self .ovc__body { align-items: flex-end; max-width: 85%; }
.ovc--wide .ovc__body { max-width: min(calc(100% - 2.5rem), 34rem); }

.ovc__who {
  display: flex;
  gap: 0.35rem;
  align-items: center;
  margin: 0 0 0.15rem 0.1rem;
  color: var(--ovc-mfg);
  font-size: 0.875rem;
  line-height: 1.4;
}
.ovc__badge {
  display: inline-block;
  padding: 0 0.3rem;
  border: 1px solid color-mix(in oklab, var(--ovc-accent) 55%, transparent);
  border-radius: 0.3rem;
  color: var(--ovc-accent);
  font-size: 0.75rem;
  font-weight: 500;
  line-height: 1.35;
}

.ovc__bubble {
  max-width: 100%;
  padding: 0.4rem 0.7rem;
  border: 1px solid var(--ovc-line);
  border-radius: 0.25rem 0.8rem 0.8rem 0.8rem;
  background: var(--ovc-bg);
  overflow-wrap: anywhere;
  word-break: normal;
}
.ovc__msg.is-self .ovc__bubble {
  border-color: color-mix(in oklab, var(--ovc-accent) 22%, var(--ovc-line));
  border-radius: 0.8rem 0.25rem 0.8rem 0.8rem;
  background: var(--ovc-self);
}
.ovc__msg.is-bot .ovc__bubble { border-color: color-mix(in oklab, var(--ovc-accent) 40%, var(--ovc-line)); }
.ovc__p + .ovc__p { margin-top: 0.55em; }
.ovc__at { color: var(--ovc-accent); font-weight: 500; }
.ovc__code {
  padding: 0.05em 0.3em;
  border-radius: 0.3em;
  background: color-mix(in oklab, var(--ovc-fg) 7%, transparent);
  overflow-wrap: anywhere;
}
.ovc__redacted {
  border-radius: 0.25em;
  background: color-mix(in oklab, var(--ovc-fg) 24%, transparent);
  color: transparent;
  -webkit-box-decoration-break: clone;
  box-decoration-break: clone;
}

.ovc__quote {
  display: -webkit-box;
  margin: 0.1rem 0 0.4rem;
  padding: 0.1rem 0 0.1rem 0.5rem;
  overflow: hidden;
  border-left: 3px solid color-mix(in oklab, var(--ovc-accent) 35%, var(--ovc-line));
  color: var(--ovc-mfg);
  font-size: 0.875rem;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}
.ovc__quote-who { margin-right: 0.4em; font-weight: 600; }

.ovc__reacts { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.25rem; }
.ovc__react {
  display: inline-flex;
  gap: 0.2rem;
  align-items: center;
  padding: 0 0.45rem;
  border: 1px solid var(--ovc-line);
  border-radius: 999px;
  background: var(--ovc-bg);
  color: var(--ovc-mfg);
  font-size: 0.875rem;
  line-height: 1.6;
}

.ovc__media { width: 22rem; max-width: 100%; }
.ovc--wide .ovc__media { width: 28rem; }
.ovc__media img {
  width: 100%;
  border: 1px solid var(--ovc-line);
  border-radius: 0.6rem;
  background: var(--ovc-bg);
}

.ovc__voice {
  display: inline-flex;
  gap: 0.5rem;
  align-items: center;
  min-width: 6.5rem;
  color: var(--ovc-mfg);
}
.ovc__wave { display: inline-flex; gap: 2px; align-items: center; height: 1rem; }
.ovc__wave span {
  display: block;
  width: 3px;
  height: 40%;
  border-radius: 2px;
  background: var(--ovc-mfg);
}
.ovc__wave span:nth-child(2), .ovc__wave span:nth-child(4) { height: 75%; }
.ovc__wave span:nth-child(3) { height: 100%; }

.ovc__card {
  width: 15.5rem;
  max-width: 100%;
  padding: 0.55rem 0.7rem 0.65rem;
  border: 1px solid var(--ovc-line);
  border-radius: 0.6rem;
  background: var(--ovc-bg);
  font-size: 0.875rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.ovc__msg.is-bot .ovc__card { border-color: color-mix(in oklab, var(--ovc-accent) 40%, var(--ovc-line)); }
.ovc__card.is-inline,
.ovc__msg.is-bot .ovc__card.is-inline {
  width: auto;
  margin-top: 0.45rem;
  padding: 0;
  border: 0;
  background: none;
}
.ovc__card-label { margin: -0.1rem 0 0.4rem; color: var(--ovc-mfg); font-size: 0.875rem; }
.ovc__card-label::before {
  content: "";
  display: inline-block;
  width: 0.55rem;
  height: 0.55rem;
  margin-right: 0.35rem;
  border: 1.5px solid currentColor;
  border-radius: 2px;
  vertical-align: 0.05em;
}
.ovc__card-title { margin: 0.1rem 0 0.3rem; font-size: 0.9375rem; font-weight: 600; line-height: 1.45; }
.ovc__card-h { margin: 0.55rem 0 0.15rem; font-weight: 600; }
.ovc__card-t { margin: 0.35rem 0 0; }
.ovc__card-hr { height: 0; margin: 0.5rem 0; border-top: 1px solid var(--ovc-line); }
.ovc__bars span {
  display: block;
  height: 0.55rem;
  margin: 0.45rem 0;
  border-radius: 0.3rem;
  background: var(--ovc-skel);
}
.ovc__bars.is-bullets span { position: relative; max-width: calc(100% - 1rem); margin-left: 1rem; }
.ovc__bars.is-bullets span::before {
  content: "";
  position: absolute;
  top: 50%;
  left: -0.8rem;
  width: 0.35rem;
  height: 0.35rem;
  margin-top: -0.175rem;
  border-radius: 50%;
  background: color-mix(in oklab, var(--ovc-accent) 60%, transparent);
}
.ovc__card-plot {
  position: relative;
  height: 7.5rem;
  margin: 0.25rem 0 0.1rem;
  overflow: hidden;
  border-radius: 0.4rem;
  background-color: var(--ovc-surface);
  background-image:
    linear-gradient(var(--ovc-skel) 1px, transparent 1px),
    linear-gradient(90deg, var(--ovc-skel) 1px, transparent 1px);
  background-size: 2.5rem 2.5rem;
}
.ovc__card-plot span {
  position: absolute;
  width: 0.5rem;
  height: 0.5rem;
  margin: -0.25rem 0 0 -0.25rem;
  border-radius: 50%;
  background: color-mix(in oklab, var(--ovc-accent) 75%, var(--ovc-bg));
}
.ovc__card-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.3rem;
  margin: 0.4rem 0;
}
.ovc__card-stats > div {
  display: flex;
  flex-direction: column;
  padding: 0.25rem 0.35rem;
  border-radius: 0.3rem;
  background: color-mix(in oklab, var(--ovc-fg) 5%, transparent);
}
.ovc__card-k { color: var(--ovc-mfg); }
.ovc__card-v { font-variant-numeric: tabular-nums; font-weight: 600; }
.ovc__card-grid { display: flex; flex-wrap: wrap; gap: 0.3rem; margin: 0.3rem 0 0.1rem; }
.ovc__card-grid span {
  display: block;
  width: 1.55rem;
  height: 1.55rem;
  border-radius: 0.3rem;
  background: var(--ovc-skel);
}

@media (max-width: 30rem) {
  .ovc__log { --ovc-px: 0.55rem; }
  .ovc__body { max-width: calc(100% - 2.75rem); }
  .ovc__msg.is-self .ovc__body { max-width: 90%; }
}
`;

/** Render once per page, before or after the first <Chat />. */
export function ChatStyles() {
  return <style>{CSS}</style>;
}
