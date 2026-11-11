import type { ComponentType, SVGProps } from 'react';

import { NextjsIcon } from './nextjs-icon.js';

import rules from '../../../icons/file-type-rules.json';
import audio from '../../../icons/svg/audio.svg?react';
import astro from '../../../icons/svg/file-type/astro.svg?react';
import babel from '../../../icons/svg/file-type/babel.svg?react';
import bash from '../../../icons/svg/file-type/bash.svg?react';
import biome from '../../../icons/svg/file-type/biome.svg?react';
import bootstrap from '../../../icons/svg/file-type/bootstrap.svg?react';
import browserslist from '../../../icons/svg/file-type/browserslist.svg?react';
import bun from '../../../icons/svg/file-type/bun.svg?react';
import c from '../../../icons/svg/file-type/c.svg?react';
import claude from '../../../icons/svg/file-type/claude.svg?react';
import cpp from '../../../icons/svg/file-type/cpp.svg?react';
import css from '../../../icons/svg/file-type/css.svg?react';
import database from '../../../icons/svg/file-type/database.svg?react';
import defaultIcon from '../../../icons/svg/file-type/default.svg?react';
import pdf from '../../../icons/svg/pdf.svg?react';
import ppt from '../../../icons/svg/ppt.svg?react';
import video from '../../../icons/svg/video.svg?react';
import word from '../../../icons/svg/word.svg?react';
import docker from '../../../icons/svg/file-type/docker.svg?react';
import eslint from '../../../icons/svg/file-type/eslint.svg?react';
import font from '../../../icons/svg/file-type/font.svg?react';
import git from '../../../icons/svg/file-type/git.svg?react';
import go from '../../../icons/svg/file-type/go.svg?react';
import graphql from '../../../icons/svg/file-type/graphql.svg?react';
import html from '../../../icons/svg/file-type/html.svg?react';
import image from '../../../icons/svg/file-type/image.svg?react';
import javascript from '../../../icons/svg/file-type/javascript.svg?react';
import json from '../../../icons/svg/file-type/json.svg?react';
import markdown from '../../../icons/svg/file-type/markdown.svg?react';
import mcp from '../../../icons/svg/file-type/mcp.svg?react';
import npm from '../../../icons/svg/file-type/npm.svg?react';
import oxc from '../../../icons/svg/file-type/oxc.svg?react';
import postcss from '../../../icons/svg/file-type/postcss.svg?react';
import prettier from '../../../icons/svg/file-type/prettier.svg?react';
import python from '../../../icons/svg/file-type/python.svg?react';
import react from '../../../icons/svg/file-type/react.svg?react';
import ruby from '../../../icons/svg/file-type/ruby.svg?react';
import rust from '../../../icons/svg/file-type/rust.svg?react';
import sass from '../../../icons/svg/file-type/sass.svg?react';
import stylelint from '../../../icons/svg/file-type/stylelint.svg?react';
import svelte from '../../../icons/svg/file-type/svelte.svg?react';
import svg from '../../../icons/svg/file-type/svg.svg?react';
import svgo from '../../../icons/svg/file-type/svgo.svg?react';
import swift from '../../../icons/svg/file-type/swift.svg?react';
import table from '../../../icons/svg/file-type/table.svg?react';
import tailwind from '../../../icons/svg/file-type/tailwind.svg?react';
import terraform from '../../../icons/svg/file-type/terraform.svg?react';
import text from '../../../icons/svg/file-type/text.svg?react';
import typescript from '../../../icons/svg/file-type/typescript.svg?react';
import vite from '../../../icons/svg/file-type/vite.svg?react';
import vscode from '../../../icons/svg/file-type/vscode.svg?react';
import vue from '../../../icons/svg/file-type/vue.svg?react';
import wasm from '../../../icons/svg/file-type/wasm.svg?react';
import webpack from '../../../icons/svg/file-type/webpack.svg?react';
import yml from '../../../icons/svg/file-type/yml.svg?react';
import zig from '../../../icons/svg/file-type/zig.svg?react';
import zip from '../../../icons/svg/file-type/zip.svg?react';

/** 一个文件类型图标的形状，以组件形式渲染。A file-type icon shape, rendered as a component. */
export type FileTypeIconComponent = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * 每个图标键对应的组件。键与 {@link resolveFileTypeIconKey} 的返回值一致。
 *
 * The component for each icon key; keys match what
 * {@link resolveFileTypeIconKey} returns.
 */
const ICONS: Record<string, FileTypeIconComponent> = {
  audio,
  astro,
  babel,
  bash,
  biome,
  bootstrap,
  browserslist,
  bun,
  c,
  claude,
  cpp,
  css,
  database,
  default: defaultIcon,
  docker,
  eslint,
  font,
  git,
  go,
  graphql,
  html,
  image,
  javascript,
  json,
  markdown,
  mcp,
  nextjs: NextjsIcon,
  npm,
  oxc,
  postcss,
  prettier,
  python,
  react,
  ruby,
  rust,
  sass,
  stylelint,
  svelte,
  svg,
  svgo,
  swift,
  table,
  tailwind,
  terraform,
  text,
  typescript,
  vite,
  vscode,
  vue,
  wasm,
  webpack,
  yml,
  zig,
  pdf,
  ppt,
  video,
  word,
  zip,
};

interface Rules {
  names: Record<string, string>;
  extensions: Record<string, string>;
  colors: Record<string, { light: string; dark: string }>;
}

const { names, extensions, colors } = rules as Rules;

/**
 * 文件名与扩展名规则。扩展名到图标键的映射是数据，无法用文件名表达，因此单独保存。
 *
 * Name and extension rules. The extension-to-key mapping is data rather than
 * artwork, so it is kept on its own.
 */
export const FILE_TYPE_RULES = { names, extensions } as const;

/** 图标键的品牌色。未登记的键回退到正文色。Brand colour per icon key. */
export function getFileTypeColor(
  key: string,
  scheme: 'light' | 'dark' = 'light',
): string {
  return colors[key]?.[scheme] ?? 'currentColor';
}

/**
 * 补充扩展名映射，优先级高于图标集自带规则，使办公与媒体格式命中彩色图标。
 *
 * Supplemental extension mapping. It takes precedence over the icon set's own
 * rules so office and media formats reach the colour icons.
 */
const SUPPLEMENTAL_EXTENSIONS: Record<string, string> = {
  pdf: 'pdf',
  doc: 'word',
  docx: 'word',
  ppt: 'ppt',
  pptx: 'ppt',
  epub: 'text',
  mp3: 'audio',
  wav: 'audio',
  m4a: 'audio',
  aac: 'audio',
  flac: 'audio',
  ogg: 'audio',
  opus: 'audio',
  wma: 'audio',
  aiff: 'audio',
  mp4: 'video',
  avi: 'video',
  mov: 'video',
  webm: 'video',
  mkv: 'video',
  m4v: 'video',
  wmv: 'video',
  flv: 'video',
  mpeg: 'video',
  mpg: 'video',
};

/**
 * 按文件名判定图标键：先查完整文件名，再从长到短拼接扩展名匹配，未命中回退
 * `default`。多段扩展名（例如 `tar.gz`）会被优先匹配。
 *
 * Resolves the icon key for a file name: an exact name match first, then compound
 * extensions from longest to shortest, falling back to `default`. Multi-part
 * extensions such as `tar.gz` therefore win over the shorter `gz`.
 */
export function resolveFileTypeIconKey(name: string): string {
  const filename = (name.split('/').pop() ?? '').toLowerCase();
  const segments = filename.split('.');

  const token =
    names[filename] ??
    segments
      .slice(1)
      .map(
        (_, index) =>
          SUPPLEMENTAL_EXTENSIONS[segments.slice(index + 1).join('.')] ??
          extensions[segments.slice(index + 1).join('.')],
      )
      .find(Boolean);

  return token && Object.hasOwn(ICONS, token) ? token : 'default';
}

/**
 * 取得文件名对应的图标组件。未命中规则时回退到通用文件图标。
 *
 * Returns the icon component for a file name, falling back to the generic file
 * icon when nothing matches.
 */
export function getFileTypeIcon(name: string): FileTypeIconComponent {
  return ICONS[resolveFileTypeIconKey(name)] ?? defaultIcon;
}
