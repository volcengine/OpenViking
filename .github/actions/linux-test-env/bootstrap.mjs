import { execFileSync } from 'node:child_process';
import { appendFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

const env = process.env;
const workspace = env.GITHUB_WORKSPACE;
// Checkout can recreate the workspace as root inside a container. Its parent
// is created by the host runner and retains the owner needed by host jobs.
const owner = execFileSync('stat', ['-c', '%u:%g', dirname(workspace)], { encoding: 'utf8' }).trim();
const home = mkdtempSync(join(env.RUNNER_TEMP, 'linux-test-home.'));
appendFileSync(env.GITHUB_STATE, `owner=${owner}\nworkspace=${workspace}\nhome=${home}\n`);
appendFileSync(env.GITHUB_ENV, `HOME=${home}\nCARGO_HOME=${home}/.cargo\nRUSTUP_HOME=${home}/.rustup\n`);
// Checkout used the original container HOME; restrict trust to these workspaces.
writeFileSync(join(home, '.gitconfig'),
  `[safe]\n\tdirectory = ${workspace}\n`);
