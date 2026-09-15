import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function resolveRelease(metadata, sourceCommit, date) {
  if (!metadata || typeof metadata.versions !== 'object' || !metadata.versions) {
    throw new Error('Registry response is missing versions');
  }
  const existing = Object.values(metadata.versions).find(pkg => pkg.openvikingSourceCommit === sourceCommit);
  if (existing) return { version: existing.version, published: true };
  let max = -1;
  for (const version of Object.keys(metadata.versions)) {
    if (version === date) max = Math.max(max, 0);
    else if (version.startsWith(`${date}-`)) {
      const suffix = version.slice(date.length + 1);
      if (/^\d+$/.test(suffix)) max = Math.max(max, Number(suffix));
    }
  }
  return { version: max < 0 ? date : `${date}-${max + 1}`, published: false };
}

async function main() {
  const sourceCommit = process.env.SOURCE_COMMIT;
  if (!/^[a-f0-9]{40}$/.test(sourceCommit || '')) throw new Error('Missing source commit');
  const packagePath = 'examples/opencode-plugin/package.json';
  const pkg = JSON.parse(readFileSync(packagePath, 'utf8'));
  if (pkg.name !== '@openviking/opencode-plugin') throw new Error('Unexpected package');
  let metadata;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch(`https://registry.npmjs.org/${encodeURIComponent(pkg.name)}`, {
        signal: AbortSignal.timeout(30000),
      });
      if (!response.ok) throw new Error(`Registry HTTP ${response.status}`);
      metadata = await response.json();
      break;
    } catch (error) {
      if (attempt === 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 2000 * (attempt + 1)));
    }
  }
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric',
  }).formatToParts(new Date());
  const part = type => parts.find(p => p.type === type).value;
  const date = `${part('year')}.${part('month')}.${part('day')}`;
  const release = resolveRelease(metadata, sourceCommit, date);
  if (!release.published) {
    pkg.version = release.version;
    pkg.openvikingSourceCommit = sourceCommit;
    writeFileSync(packagePath, `${JSON.stringify(pkg, null, 2)}\n`);
  }
  appendFileSync(process.env.GITHUB_OUTPUT, `version=${release.version}\npublished=${release.published}\n`);
  console.log(`${pkg.name}@${release.version}: ${release.published ? 'already published from this commit' : 'ready to publish'}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
