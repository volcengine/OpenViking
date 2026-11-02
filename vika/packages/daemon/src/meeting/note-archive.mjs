// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { OVArchive } from '../ov-archive.mjs';
import { OVConnection, readOVConfig } from '../ov-connection.mjs';

// The Feishu/Lark AI note of each meeting the owner attended, stored in the
// owner's private OpenViking area so recall can find it later:
//
//   <root>/<meeting_id>/
//     note.md               the meeting (topic, time, people) and its note:
//                           summary, todos, links, related notes
//     transcript.md         the verbatim transcript: speaker, time, words
//     chunks/note/          note.md split for recall, when long
//     chunks/transcript/    transcript.md split for recall, when long
//
// The chunk folders are siblings: an import to one replaces only what is in
// it. What is stored is a snapshot. Feishu keeps changing its own copy (a
// later meeting of a series links back to it, people tick off todos), so
// note.md says which revision it is and where the live one is.

const MEETING = /^\d{1,32}$/;
const PARTS = new Set(['note', 'transcript']);
const MAX_NAMES = 50;

export function meetingNotesRoot(user) {
  return user ? `viking://user/${user}/resources/meetings` : 'viking://~/resources/meetings';
}

export function meetingFolder(root, meetingId) {
  if (!MEETING.test(String(meetingId))) throw new Error('not a meeting id');
  return `${root}/${meetingId}`;
}

// What read_meeting_note reads for a uri in the archive: { target, anchor? },
// the whole note or transcript. A chunk leads to the whole text it was cut
// from, with itself as the anchor where reading starts. Null for any uri that
// is not part of a stored meeting.
export function meetingReadTarget(root, uri) {
  if (!String(uri).startsWith(`${root}/`)) return null;
  const parts = uri.slice(root.length + 1).replace(/\/+$/, '').split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..') || !MEETING.test(parts[0])) return null;
  const [id, ...rest] = parts;
  const folder = `${root}/${id}`;
  if (rest.length === 0) return { target: `${folder}/note.md` };
  if (rest.length === 1 && PARTS.has(rest[0].replace(/\.md$/, '')) && rest[0].endsWith('.md')) return { target: `${folder}/${rest[0]}` };
  if (rest[0] === 'chunks' && PARTS.has(rest[1])) return { target: `${folder}/${rest[1]}.md`, ...(rest.length > 2 ? { anchor: uri } : {}) };
  return null;
}

// A document as `docs +fetch --doc-format im-markdown` gives it, as plain
// Markdown: a mention is @Name, a whiteboard a marker, embedded app blocks
// (nothing readable) dropped.
export function cleanDocMarkdown(text) {
  return String(text ?? '')
    .replace(/<at\b[^>]*>([^<]*)<\/at>/g, (_, name) => (name.trim() ? `@${name.trim()}` : ''))
    .replace(/<whiteboard\b[^>]*>\s*<\/whiteboard>|<whiteboard\b[^>]*\/>/g, '[whiteboard]')
    .replace(/<readonly-block\b[^>]*>[\s\S]*?<\/readonly-block>|<readonly-block\b[^>]*\/>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// A document's text under our own title: its own first-line title goes, and
// its headings move one level down.
function bodyOf(doc) {
  const lines = cleanDocMarkdown(doc).split('\n');
  if (/^#\s/.test(lines[0] ?? '')) lines.shift();
  let fenced = false;
  return lines.map((line) => {
    if (/^\s*```/.test(line)) fenced = !fenced;
    return !fenced && /^#{1,5}\s/.test(line) ? `#${line}` : line;
  }).join('\n').trim();
}

// What the note calls the meeting: its own title ("智能纪要：<what it was
// about> <date>"), which says more than a default topic such as "X 的视频会议".
export function noteTitle(doc) {
  const first = cleanDocMarkdown(doc).split('\n')[0] ?? '';
  if (!/^#\s/.test(first)) return null;
  const title = first.replace(/^#\s+/, '').replace(/^(?:智能纪要|文字记录)\s*[:：]\s*/, '')
    .replace(/\s*\d{4}年\d{1,2}月\d{1,2}日\s*$/, '').trim();
  return title || null;
}

function label(value, max = 200) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}

const minute = (ms) => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');

function timeLine({ startMs, endMs }) {
  if (!startMs) return null;
  if (!endMs || endMs < startMs) return `${minute(startMs)} UTC`;
  return `${minute(startMs)} – ${minute(endMs).slice(11)} UTC (${Math.max(1, Math.round((endMs - startMs) / 60_000))} min)`;
}

function meetingLines(meeting, title) {
  const lines = [`- Meeting: ${meeting.id}${meeting.number ? ` (number ${meeting.number})` : ''}`];
  if (meeting.topic && label(meeting.topic) !== title) lines.push(`- Meeting topic: ${label(meeting.topic)}`);
  const time = timeLine(meeting);
  if (time) lines.push(`- Time: ${time}`);
  if (meeting.host) lines.push(`- Host: ${label(meeting.host, 100)}`);
  const names = (meeting.participants ?? []).map((person) => label(person.name, 100)).filter(Boolean);
  if (meeting.participantCount || names.length) {
    const count = meeting.participantCount ?? names.length;
    const shown = names.slice(0, MAX_NAMES).join(', ');
    lines.push(`- Participants (${count}): ${shown || 'names not available'}${names.length > MAX_NAMES ? ', …' : ''}`);
  }
  if (meeting.external) lines.push('- People from outside the owner\'s organization took part');
  if (meeting.calendarEventId) lines.push(`- Calendar event: ${meeting.calendarEventId}`);
  return lines;
}

// note.md: the meeting under the note's title, then the note's own text. `note`: { id, docToken,
// verbatimToken, revision }; `summary`: the note document as fetched.
export function noteMarkdown({ meeting, note, summary, transcriptUri, storedAtMs }) {
  const title = label(noteTitle(summary) ?? meeting.topic) || 'Meeting';
  const lines = [`# ${title}`, '', ...meetingLines(meeting, title)];
  lines.push(`- Feishu note: document ${note.docToken ?? 'none'}${note.revision != null ? `, revision ${note.revision}` : ''}; `
    + `stored ${new Date(storedAtMs).toISOString()}. Feishu's own copy may have changed since.`);
  if (transcriptUri) lines.push(`- Transcript: ${transcriptUri}${note.verbatimToken ? ` (Feishu document ${note.verbatimToken})` : ''}`);
  const body = summary ? bodyOf(summary) : '';
  lines.push('', body || '(the note has no summary)');
  return `${lines.join('\n')}\n`;
}

// transcript.md: what was said, under the meeting it was said in.
export function transcriptMarkdown({ meeting, transcript, noteUri, title = null }) {
  const time = timeLine(meeting);
  return `${[
    `# Transcript: ${label(title ?? noteTitle(transcript) ?? meeting.topic) || 'Meeting'}`, '',
    `- Meeting: ${meeting.id}`,
    ...(time ? [`- Time: ${time}`] : []),
    `- Note: ${noteUri}`,
    '- Speakers are as Feishu transcribed them; everyone at one meeting-room microphone shows as one speaker.',
    '', bodyOf(transcript),
  ].join('\n')}\n`;
}

export class MeetingNoteArchive extends OVArchive {
  static fromConfigFile(file, options) {
    return new MeetingNoteArchive(new OVConnection(readOVConfig(file)), options);
  }

  constructor(connection, options = {}) {
    super(connection, { ...options, kind: 'meeting_note' });
    this.root = meetingNotesRoot(connection.user);
  }

  folder(meetingId) {
    return meetingFolder(this.root, meetingId);
  }

  // The whole texts first: the meeting is stored once they are in place,
  // and the chunks only add recall. Resolves with where everything went.
  async store(meetingId, { note, transcript = null }) {
    const folder = this.folder(meetingId);
    const noteUri = await this.write(`${folder}/note.md`, note);
    const transcriptUri = transcript === null ? null : await this.write(`${folder}/transcript.md`, transcript);
    let chunked = 0;
    if (await this.chunk(`${folder}/chunks/note`, note, 'note.md')) chunked += 1;
    if (transcript !== null && await this.chunk(`${folder}/chunks/transcript`, transcript, 'transcript.md')) chunked += 1;
    return { folder, noteUri, transcriptUri, chunked };
  }
}
