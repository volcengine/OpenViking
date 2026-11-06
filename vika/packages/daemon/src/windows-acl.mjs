// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFileSync } from 'node:child_process';
import path from 'node:path';

// The script is fixed; a directory name is data in the environment, never code.
// Existing directories are checked only. Only the caller's newly created,
// dedicated directory receives a protected current-user-only DACL.
const SCRIPT = `
$ErrorActionPreference = 'Stop'
$directory = $env:VIKA_PRIVATE_DIRECTORY
$item = Get-Item -LiteralPath $directory -Force
if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw 'Private directory must be a real directory, not a reparse point'
}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:VIKA_PRIVATE_DIRECTORY_CREATED -eq '1') {
  $acl = New-Object Security.AccessControl.DirectorySecurity
  $acl.SetOwner($sid)
  $acl.SetAccessRuleProtection($true, $false)
  $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
  $acl.AddAccessRule($rule)
  Set-Acl -LiteralPath $directory -AclObject $acl
}
$acl = Get-Acl -LiteralPath $directory
if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) {
  throw 'Private directory must be owned by the current user'
}
if (-not $acl.AreAccessRulesProtected) { throw 'Private directory must disable inherited access' }
$fullControl = $false
foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
  if ($rule.IsInherited) { throw 'Private directory contains an inherited access rule' }
  if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow) {
    if ($rule.IdentityReference.Value -ne $sid.Value) { throw 'Private directory allows another identity' }
    if (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl) {
      $fullControl = $true
    }
  }
}
if (-not $fullControl) { throw 'Private directory must grant its owner full control' }
`;
const ENCODED_SCRIPT = Buffer.from(SCRIPT, 'utf16le').toString('base64');

export function assertWindowsPrivateDirectory(directory, { created = false } = {}) {
  const systemRoot = process.env.SystemRoot || process.env.SYSTEMROOT;
  if (!systemRoot || !path.win32.isAbsolute(systemRoot)) throw new Error('Windows SystemRoot is unavailable');
  const powershell = path.win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  try {
    execFileSync(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', ENCODED_SCRIPT], {
      windowsHide: true, timeout: 30_000, stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, VIKA_PRIVATE_DIRECTORY: directory, VIKA_PRIVATE_DIRECTORY_CREATED: created ? '1' : '0' },
    });
  } catch {
    throw new Error('Windows private directory ACL validation failed: require current-user ownership, protected inheritance, and current-user-only full access');
  }
}
