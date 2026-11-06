// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { MailArchive } from '../src/email/archive.mjs';
import { MeetingNoteArchive } from '../src/meeting/note-archive.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { OVSessionClient } from '../src/ov-session-context.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

const quiet = { debug() {}, info() {}, warn() {}, error() {} };

for (const user of [undefined, 'old-owner']) {
  test(`private archives follow the key when the configured user is ${user ?? 'absent'}`, async (t) => {
    const { state, url } = await fakeOV(t, { apiKeys: { 'instance-key': 'owner.asd' } });
    const config = { url, api_key: 'instance-key', ...(user ? { user } : {}) };
    const connection = new OVConnection(config);
    const mail = new MailArchive(connection);
    const meeting = new MeetingNoteArchive(connection);
    const mailUri = `${mail.folder('mail:abcd1234:1:1')}/body.md`;
    const noteUri = `${meeting.folder('734000000000000301')}/note.md`;
    await mail.write(mailUri, 'Private mail for this instance');
    await meeting.write(noteUri, 'Private meeting for this instance');
    const canonical = (uri) => uri.replace('viking://~', 'viking://user/owner.asd');
    assert.deepEqual([...state.files.keys()], [canonical(mailUri), canonical(noteUri)]);

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-user-paths-'));
    const configPath = path.join(dir, 'ov.json');
    fs.writeFileSync(configPath, JSON.stringify(config));
    const store = new Store(path.join(dir, 'vika.sqlite3'), quiet);
    t.after(() => { store.close(); fs.rmSync(dir, { recursive: true, force: true }); });
    store.initializeCognition('codex', 'token');
    const actions = new ActionService({ config: { meetingNotes: true }, store,
      connectors: fakeConnectors({ ids: ['lark', 'email'] }),
      ovClient: new OVSessionClient(configPath), logger: quiet });
    const call = (tool, input) => actions.call(tool, input, { authority: 'token' });

    for (const [tool, uri, text, code] of [
      ['read_email', mailUri, /Private mail/, 'outside_mail_archive'],
      ['read_meeting_note', noteUri, /Private meeting/, 'outside_meeting_archive'],
    ]) {
      // Connector links use ~; search results use the authenticated user's URI.
      assert.match((await call(tool, { uri })).text, text);
      assert.match((await call(tool, { uri: canonical(uri) })).text, text);
      for (const foreign of [
        uri.replace('viking://~', 'viking://user/old-owner'),
        uri.replace('viking://~', 'viking://user/owner.asd/peers/someone'),
      ]) {
        state.files.set(foreign, 'Other private data');
        const before = state.requests.length;
        await assert.rejects(call(tool, { uri: foreign }), (error) => error.code === code);
        assert.ok(!state.requests.slice(before).some(({ query }) => query.uri === foreign),
          'a foreign archive must be rejected before reading its content');
      }
    }
    assert.match((await call('read_meeting_note', { meeting_id: '734000000000000301' })).text, /Private meeting/);
  });
}
