// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { pathToFileURL } from 'node:url';

// `import()` parses its argument as a URL. A bare Windows path is read as the
// scheme `c:` and rejected with ERR_UNSUPPORTED_ESM_URL_SCHEME, while the same
// path with POSIX separators happens to resolve as a relative URL. Everything
// handed to `import()` goes through here first, so both platforms get a
// specifier the ESM loader accepts.
export function importableModuleUrl(resolved) {
  return pathToFileURL(resolved).href;
}
