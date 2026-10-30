import { execFileSync } from 'node:child_process';
import { appendFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const env = process.env;
const workspace = env.GITHUB_WORKSPACE;
// Preserve the host runner's ownership before any build writes root-owned files.
const owner = execFileSync('stat', ['-c', '%u:%g', workspace], { encoding: 'utf8' }).trim();
const home = mkdtempSync(join(env.RUNNER_TEMP, 'linux-test-home.'));
appendFileSync(env.GITHUB_STATE, `owner=${owner}\nworkspace=${workspace}\nhome=${home}\n`);
if (env['INPUT_INSTALL-DEPENDENCIES'] !== 'false') {
  execFileSync('bash', ['-e', '-c', `
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl git sudo build-essential cmake pkg-config \
    zip unzip xz-utils libssl-dev libicu74 python3 ffmpeg
`], { stdio: 'inherit' });
}
appendFileSync(env.GITHUB_ENV, `HOME=${home}\nCARGO_HOME=${home}/.cargo\nRUSTUP_HOME=${home}/.rustup\n`);
// Checkout used the original container HOME; restrict trust to these workspaces.
writeFileSync(join(home, '.gitconfig'),
  `[safe]\n\tdirectory = ${workspace}\n\tdirectory = ${workspace}/.ci/hermes-agent\n`);
