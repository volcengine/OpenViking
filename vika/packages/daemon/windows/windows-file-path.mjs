// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
export function assertWindowsFilePath(value) {
  const name = String(value);
  if (/^[A-Za-z]:(?:$|[^\\/])/.test(name)) throw new Error('Drive-relative Windows file paths are ambiguous; use an absolute path');
  // Drive colon is allowed; all other colons could select NTFS streams.
  const relative = name.replace(/^[A-Za-z]:/, '');
  for (const part of relative.split(/[\\/]/)) {
    if (!part || part === '.' || part === '..') continue;
    if (/[<>:"|?*\x00-\x1f]/.test(part) || /[. ]$/.test(part)
      || /^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/i.test(part)) {
      throw new Error('Invalid Windows file path: reserved name, character, trailing dot/space or alternate data stream');
    }
  }
}
export function guardWindowsFileTool(tool) {
  return { ...tool, async execute(args, ...rest) {
    assertWindowsFilePath(args.path);
    return tool.execute(args, ...rest);
  } };
}
