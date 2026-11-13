import Prism from 'prismjs';
import type { FilePreviewSource } from './types.js';

const aliases: Record<string, string> = {
  'application/json': 'json',
  'application/x-ndjson': 'json',
  'text/markdown': 'markdown',
  'application/javascript': 'javascript',
  'text/javascript': 'javascript',
  'application/typescript': 'typescript',
  'text/typescript': 'typescript',
  'application/yaml': 'yaml',
  'application/x-yaml': 'yaml',
  'text/yaml': 'yaml',
  'application/xml': 'markup',
  'text/xml': 'markup',
  'text/html': 'markup',
  'text/css': 'css',
  'text/x-python': 'python',
  js: 'javascript',
  cjs: 'javascript',
  mjs: 'javascript',
  ts: 'typescript',
  py: 'python',
  pyw: 'python',
  rb: 'ruby',
  rake: 'ruby',
  gemspec: 'ruby',
  rs: 'rust',
  cs: 'csharp',
  kt: 'kotlin',
  kts: 'kotlin',
  sh: 'bash',
  zsh: 'bash',
  md: 'markdown',
  mdx: 'markdown',
  html: 'markup',
  htm: 'markup',
  xhtml: 'markup',
  xml: 'markup',
  svg: 'markup',
  yml: 'yaml',
  jsonl: 'json',
  ndjson: 'json',
  h: 'c',
  cc: 'cpp',
  hpp: 'cpp',
  cxx: 'cpp',
  hxx: 'cpp',
  cfg: 'ini',
  conf: 'ini',
  ex: 'elixir',
  exs: 'elixir',
  erl: 'erlang',
  hrl: 'erlang',
  hs: 'haskell',
  lhs: 'haskell',
  m: 'objectivec',
  mm: 'objectivec',
  pl: 'perl',
  pm: 'perl',
  rmd: 'r',
  gql: 'graphql',
  tex: 'latex',
  wat: 'wasm',
  patch: 'diff',
  shell: 'bash',
  dockerfile: 'docker',
  proto: 'protobuf',
};
const loaders: Record<string, () => Promise<unknown>> = {
  bash: () => import('prismjs/components/prism-bash.js'),
  c: () => import('prismjs/components/prism-c.js'),
  cpp: () => import('prismjs/components/prism-cpp.js'),
  csharp: () => import('prismjs/components/prism-csharp.js'),
  dart: () => import('prismjs/components/prism-dart.js'),
  diff: () => import('prismjs/components/prism-diff.js'),
  docker: () => import('prismjs/components/prism-docker.js'),
  elixir: () => import('prismjs/components/prism-elixir.js'),
  erlang: () => import('prismjs/components/prism-erlang.js'),
  fish: () => import('./prism-fish.js'),
  go: () => import('prismjs/components/prism-go.js'),
  graphql: () => import('prismjs/components/prism-graphql.js'),
  haskell: () => import('prismjs/components/prism-haskell.js'),
  ini: () => import('prismjs/components/prism-ini.js'),
  java: () => import('prismjs/components/prism-java.js'),
  json: () => import('prismjs/components/prism-json.js'),
  jsx: () => import('prismjs/components/prism-jsx.js'),
  kotlin: () => import('prismjs/components/prism-kotlin.js'),
  latex: () => import('prismjs/components/prism-latex.js'),
  less: () => import('prismjs/components/prism-less.js'),
  lua: () => import('prismjs/components/prism-lua.js'),
  makefile: () => import('prismjs/components/prism-makefile.js'),
  markdown: () => import('prismjs/components/prism-markdown.js'),
  'markup-templating': () =>
    import('prismjs/components/prism-markup-templating.js'),
  nginx: () => import('prismjs/components/prism-nginx.js'),
  objectivec: () => import('prismjs/components/prism-objectivec.js'),
  perl: () => import('prismjs/components/prism-perl.js'),
  php: () => import('prismjs/components/prism-php.js'),
  protobuf: () => import('prismjs/components/prism-protobuf.js'),
  python: () => import('prismjs/components/prism-python.js'),
  r: () => import('prismjs/components/prism-r.js'),
  ruby: () => import('prismjs/components/prism-ruby.js'),
  rust: () => import('prismjs/components/prism-rust.js'),
  scala: () => import('prismjs/components/prism-scala.js'),
  scss: () => import('prismjs/components/prism-scss.js'),
  sql: () => import('prismjs/components/prism-sql.js'),
  swift: () => import('prismjs/components/prism-swift.js'),
  toml: () => import('prismjs/components/prism-toml.js'),
  tsx: () => import('prismjs/components/prism-tsx.js'),
  typescript: () => import('prismjs/components/prism-typescript.js'),
  wasm: () => import('prismjs/components/prism-wasm.js'),
  yaml: () => import('prismjs/components/prism-yaml.js'),
};
const dependencies: Record<string, string[]> = {
  cpp: ['c'],
  fish: ['bash'],
  jsx: ['markup', 'javascript'],
  tsx: ['jsx', 'typescript'],
  typescript: ['javascript'],
  markdown: ['markup'],
  less: ['css'],
  scss: ['css'],
  objectivec: ['c'],
  php: ['markup-templating'],
  'markup-templating': ['markup'],
  scala: ['java'],
};

/** Use the same Prism module and grammars as FileViewer, without private imports. */
export function prismLanguage(
  source: Pick<FilePreviewSource, 'fileName' | 'fileType'>,
) {
  const hint = source.fileType?.split(';')[0]?.trim().toLowerCase();
  if (hint) {
    const language = Object.hasOwn(aliases, hint) ? aliases[hint]! : hint;
    if (
      Object.hasOwn(loaders, language) ||
      Object.hasOwn(Prism.languages, language)
    )
      return language;
    if (['text', 'txt', 'plaintext', 'text/plain'].includes(hint))
      return 'plaintext';
  }
  const name = (source.fileName.split(/[\\/]/).pop() || '')
    .trim()
    .toLowerCase();
  const extension = name.split('.').pop()!;
  if (Object.hasOwn(aliases, extension)) return aliases[extension]!;
  if (
    Object.hasOwn(loaders, extension) ||
    Object.hasOwn(Prism.languages, extension)
  )
    return extension;
  if (name.startsWith('dockerfile.')) return 'docker';
  if (name === 'gnumakefile') return 'makefile';
  if (['.bashrc', '.zshrc', '.bash_profile'].includes(name)) return 'bash';
  return extension;
}

export async function loadPrismLanguage(
  language: string,
): Promise<Prism.Grammar | undefined> {
  const required = Object.hasOwn(dependencies, language)
    ? dependencies[language]
    : [];
  await Promise.all((required ?? []).map(loadPrismLanguage));
  // FileViewer may already have registered the grammar on this shared instance.
  if (
    !Object.hasOwn(Prism.languages, language) &&
    Object.hasOwn(loaders, language)
  )
    await loaders[language]!();
  const grammar = Prism.languages[language];
  return Object.hasOwn(Prism.languages, language) &&
    grammar &&
    typeof grammar === 'object'
    ? grammar
    : undefined;
}

export { Prism };
