import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export default {
  watch: ['../../en/**/*.md', '../../zh/**/*.md'],
  load() {
    const root = fileURLToPath(new URL('../../', import.meta.url))
    return ['en', 'zh'].flatMap(locale =>
      fs.readdirSync(path.join(root, locale), { recursive: true })
        .filter(file => typeof file === 'string' && file.endsWith('.md'))
        .map(file => `${locale}/${file}`.replaceAll(path.sep, '/')))
  }
}
