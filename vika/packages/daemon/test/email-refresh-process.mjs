// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A real second process for the account-file refresh race test. The parent owns
// the token endpoint so it can observe whether both processes spend one token.
import { credentialSource, loadAccount } from '../src/email/account.mjs';

process.once('message', async ({ file, endpoint }) => {
  try {
    const account = loadAccount(file);
    const fetch = async (url, init) => {
      const response = await globalThis.fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: init.body,
      });
      return { ok: response.ok, status: response.status, text: () => response.text() };
    };
    const credential = credentialSource(account, { fetch });
    process.send({ ok: true, ...(await credential()) });
  } catch (error) {
    process.send({ ok: false, error: error.message });
  } finally {
    process.disconnect();
  }
});
