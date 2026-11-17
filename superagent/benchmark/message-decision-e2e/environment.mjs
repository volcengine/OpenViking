import fs from 'node:fs';
import path from 'node:path';

export function createIsolatedTraex(root, { authPath, binary }) {
  const home = path.join(root, 'home');
  const traeHome = path.join(home, '.trae');
  const cliHome = path.join(traeHome, 'cli');
  fs.mkdirSync(cliHome, { recursive: true, mode: 0o700 });
  fs.copyFileSync(authPath, path.join(cliHome, 'auth.json'));
  fs.chmodSync(path.join(cliHome, 'auth.json'), 0o600);
  fs.writeFileSync(path.join(traeHome, 'traecli.toml'), [
    'resource_dirs = []',
    '[features]',
    'memories = false',
    'plugins = false',
    'plugin_hooks = false',
    'multi_agent = false',
    'multi_agent_v2 = false',
    '[skills.bundled]',
    'enabled = false',
    '',
  ].join('\n'), { mode: 0o600 });
  const launcher = path.join(root, 'isolated-trae.sh');
  const quote = (value) => `'${String(value).replaceAll("'", "'\\''")}'`;
  fs.writeFileSync(launcher, `#!/bin/sh
unset CODEX_HOME CODEX_PROFILE TRAE_PROFILE TRAECLI_PROFILE
export HOME=${quote(home)}
export TRAE_HOME=${quote(traeHome)}
export TRAECLI_HOME=${quote(cliHome)}
exec ${quote(binary)} "$@"
`, { mode: 0o700 });
  return { bin: launcher, home, traeHome, cliHome };
}
