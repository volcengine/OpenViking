import { OV_PATHS } from '../../client/paths.js';
import type { Argument } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';
import { uri } from './content-command-specs.js';

const basename = (path: string) => path.split(/[\\/]/).at(-1) ?? '';
const filenamePattern = /(?:^|[\\/])(?!\.{1,2}$)[^\\/\p{Cc}]+$/u;
const localPath: Argument = { name: 'local-path', pattern: filenamePattern };
const outputPath: Argument = {
  ...localPath,
  name: 'output',
  placeholder: 'output.ovpack',
};
const includeVectors: Argument = {
  name: 'include-vectors',
  kind: 'boolean',
  valueKey: 'include_vectors',
  defaultValue: false,
};
function packFilename(path: string) {
  const name = basename(path);
  return name.toLowerCase().endsWith('.ovpack') ? name : `${name}.ovpack`;
}

export const DOWNLOAD_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'get',
    positional: [uri, localPath],
    skeleton: 'resource',
    downloadFilename: (values) => basename(values['local-path'] as string),
    request: ({ uri }) => ({
      method: 'GET',
      path: OV_PATHS.contentDownload,
      query: { uri: uri as string },
      responseType: 'blob',
    }),
  },
  ...(['export', 'backup'] as const).map((name): CommandSpec => ({
    name,
    positional: name === 'export' ? [uri, outputPath] : [outputPath],
    flags: [includeVectors],
    skeleton: 'write',
    downloadFilename: ({ output }) => packFilename(output as string),
    request: ({ uri, include_vectors }) => ({
      method: 'POST',
      path: name === 'export' ? OV_PATHS.packExport : OV_PATHS.packBackup,
      body: name === 'export' ? { uri, include_vectors } : { include_vectors },
      responseType: 'blob',
    }),
  })),
];
