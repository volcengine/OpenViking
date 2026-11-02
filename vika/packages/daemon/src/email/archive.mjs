// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import path from 'node:path';
import { OVArchive, ovOutage, readStoredText } from '../ov-archive.mjs';
import { OVConnection, readOVConfig } from '../ov-connection.mjs';

export { chunkOffset, needsChunks, readStoredText } from '../ov-archive.mjs';

// Each mail the Connector reports is stored in the owner's private OpenViking
// area before its event goes out, so the runtime gets links instead of the
// whole mail and recall can find it later:
//
//   <root>/<account tag>-<uidvalidity>-<uid>/
//     body.md                          the whole mail
//     chunks/                          a long body, split for recall
//     attachments/<NN>-<name>/full/    the whole attachment as text
//     attachments/<NN>-<name>/chunks/  a long attachment, split for recall
//
// A long text is stored whole and split again for recall (see ov-archive.mjs);
// read_email always reads the whole text. `viking://resources/...` is shared
// with the whole OV account; mail is not.

const MAIL_ID = /^mail:([0-9a-f]{8}):(\d{1,10}):(\d{1,10})$/;
const FOLDER = /^[0-9a-f]{8}-\d{1,10}-\d{1,10}$/;
// What OV's resource parsers turn into Markdown, by extension.
const PARSED = new Set([
  'pdf', 'md', 'markdown', 'mdown', 'mkd', 'txt', 'text', 'html', 'htm',
  'doc', 'docx', 'docm', 'odt', 'rtf', 'epub',
  'xlsx', 'xls', 'xlsm', 'xlsb', 'ods', 'csv',
  'pptx', 'ppt', 'pptm', 'pps', 'ppsx', 'ppsm', 'pot', 'odp',
]);
// The extension OV needs for a part that came without a file name.
const EXTENSION_FOR = {
  'application/pdf': 'pdf',
  'application/msword': 'doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
  'application/rtf': 'rtf',
  'application/epub+zip': 'epub',
  'text/csv': 'csv',
  'text/html': 'html',
  'text/markdown': 'md',
  'text/plain': 'txt',
};

export function mailArchiveRoot(user) {
  return user ? `viking://user/${user}/resources/emails` : 'viking://~/resources/emails';
}

// mail:<tag>:<uidvalidity>:<uid> -> <root>/<tag>-<uidvalidity>-<uid>
export function mailFolder(root, messageId) {
  const match = MAIL_ID.exec(String(messageId));
  if (!match) throw new Error('not a mail id');
  return `${root}/${match[1]}-${match[2]}-${match[3]}`;
}

// What read_email reads for a uri in the archive: { target, anchor? }, the
// whole text of a mail or an attachment. A chunk leads to the whole text it
// was cut from, with itself as the anchor where reading starts. Null for any
// uri that is not part of a stored mail.
export function readTarget(root, uri) {
  if (!String(uri).startsWith(`${root}/`)) return null;
  const parts = uri.slice(root.length + 1).replace(/\/+$/, '').split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..') || !FOLDER.test(parts[0])) return null;
  const [folder, ...rest] = parts;
  const mail = `${root}/${folder}`;
  if (rest.length === 0 || (rest.length === 1 && rest[0] === 'body.md')) return { target: `${mail}/body.md` };
  if (rest[0] === 'chunks') return { target: `${mail}/body.md`, ...(rest.length > 1 ? { anchor: uri } : {}) };
  if (rest[0] !== 'attachments' || rest.length < 2) return null;
  const full = `${mail}/attachments/${rest[1]}/full`;
  if (rest.length === 2 || rest[2] === 'full') return { target: full };
  if (rest[2] === 'chunks') return { target: full, ...(rest.length > 3 ? { anchor: uri } : {}) };
  return null;
}

// A sender-chosen file name as one path segment: letters, digits, . _ -.
function safeName(value, max = 80) {
  const clean = String(value ?? '').normalize('NFC')
    .replace(/[^\p{L}\p{N}._-]+/gu, '-').replace(/-+/g, '-').replace(/^[-.]+|[-.]+$/g, '');
  if (!clean) return 'attachment';
  if (clean.length <= max) return clean;
  const extension = path.extname(clean).slice(0, 12);
  return `${clean.slice(0, max - extension.length)}${extension}`;
}

function extensionOf(filename) {
  return path.extname(String(filename ?? '')).slice(1).toLowerCase();
}

// Whether OV parses a file of this name and type.
export function parsesAttachment({ filename, type }) {
  return PARSED.has(extensionOf(filename)) || Boolean(EXTENSION_FOR[type]);
}

function uploadName({ filename, type }) {
  const name = safeName(filename);
  return PARSED.has(extensionOf(name)) ? name : `${path.basename(name, path.extname(name))}.${EXTENSION_FOR[type]}`;
}

// An OV that cannot be reached, is overloaded or failed: the mail is not
// stored at all. A refusal of one file only loses that file.
const outage = ovOutage;

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export class MailArchive extends OVArchive {
  static fromConfigFile(file, options) {
    return new MailArchive(new OVConnection(readOVConfig(file)), options);
  }

  constructor(connection, options = {}) {
    super(connection, { ...options, kind: 'email' });
    this.root = mailArchiveRoot(connection.user);
  }

  folder(messageId) {
    return mailFolder(this.root, messageId);
  }

  // files: [{ name, filename, type, size, data?, text? }] in mail order. `text`
  // is written as is (a forwarded mail, a text file OV does not parse); `data`
  // is parsed by OV when it can read the format. Either lands in the
  // attachment's full/. Waits until every parsed file has landed or `deadline`
  // passes, then queues chunks of the long ones. Resolves with one entry per
  // file: { id, name, type, size, uri?, status: stored|processing|failed|skipped, reason? }.
  async storeAttachments(folder, files, { deadline }) {
    const entries = [];
    const tasks = new Map();
    const texts = new Map();
    for (const [index, file] of files.entries()) {
      const id = String(index + 1).padStart(2, '0');
      const base = safeName(file.filename);
      const uri = `${folder}/attachments/${id}-${base}`;
      const entry = { id, name: file.name, type: file.type, size: file.size };
      entries.push(entry);
      try {
        if (typeof file.text === 'string') {
          const stored = file.type === 'message/rfc822' ? 'message.md' : `${path.basename(base, path.extname(base))}.txt`;
          await this.write(`${uri}/full/${stored}`, file.text);
          Object.assign(entry, { uri, status: 'stored' });
          texts.set(entry, file.text);
        } else if (file.data && parsesAttachment(file)) {
          const task = await this.importFile(`${uri}/full`, { data: file.data, name: uploadName(file), type: file.type },
            { parse_mode: 'no_split' });
          Object.assign(entry, { uri, status: task ? 'processing' : 'stored' });
          if (task) tasks.set(entry, task);
        } else {
          Object.assign(entry, { status: 'skipped', reason: 'OpenViking does not read this type' });
        }
      } catch (error) {
        if (outage(error)) throw error;
        Object.assign(entry, { status: 'failed', reason: 'OpenViking refused it' });
      }
    }
    await this.land(tasks, deadline);
    // An attachment still being read gets no chunks: its text is not there yet.
    for (const entry of entries.filter(({ status }) => status === 'stored')) {
      let text = texts.get(entry);
      if (text === undefined) {
        try {
          text = await readStoredText((route) => this.get(route), `${entry.uri}/full`);
        } catch (error) {
          this.logger.warn('email_chunks_failed', { error: error.message });
          continue;
        }
      }
      const base = entry.uri.slice(entry.uri.lastIndexOf('/') + 1).replace(/^\d{2}-/, '');
      await this.chunk(`${entry.uri}/chunks`, text, `${path.basename(base, path.extname(base))}.md`);
    }
    return entries;
  }

  // A parse task has put its file in place once it reaches the vector queue.
  async land(tasks, deadline) {
    while (tasks.size > 0 && Date.now() < deadline) {
      for (const [entry, taskId] of [...tasks]) {
        let task;
        try {
          task = (await this.connection.json(`/api/v1/tasks/${encodeURIComponent(taskId)}`)).result;
        } catch (error) {
          if (outage(error)) throw error;
          continue;
        }
        if (task?.status === 'failed') {
          Object.assign(entry, { status: 'failed', reason: 'OpenViking could not read it' });
          delete entry.uri;
        } else if (task?.status === 'completed' || task?.stage === 'processing_queue') {
          entry.status = 'stored';
        } else {
          continue;
        }
        tasks.delete(entry);
      }
      if (tasks.size > 0) await sleep(this.pollMs);
    }
  }
}
