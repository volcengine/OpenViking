import { execFileSync } from 'node:child_process';

const { STATE_owner: owner, STATE_workspace: workspace, STATE_home: home } = process.env;
if (owner && workspace && home) {
  // Run after test/cache steps, before checkout's post-job cleanup. Do not follow
  // symlinks into host paths. The next host job (including P0) must be able to
  // clean the shared checkout and runner temp directory.
  execFileSync('chown', ['-hR', owner, workspace, home], { stdio: 'inherit' });
}
