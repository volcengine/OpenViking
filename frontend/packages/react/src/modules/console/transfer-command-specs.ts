import { OV_PATHS } from '../../client/paths.js';
import { downloadCommandFile, uploadCommandFile } from './command-files.js';
import type { Argument } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';
import { uri } from './content-command-specs.js';

const pack: Argument = { name: 'file.ovpack' };
const output: Argument = { name: 'output.ovpack' };
const vectors: Argument = {
  name: 'include-vectors',
  valueKey: 'include_vectors',
  kind: 'boolean',
  defaultValue: false,
};
const importFlags: readonly Argument[] = [
  {
    name: 'on-conflict',
    valueKey: 'on_conflict',
    choices: ['fail', 'overwrite', 'skip'],
    defaultValue: 'fail',
    placeholder: 'policy',
  },
  {
    name: 'vector-mode',
    valueKey: 'vector_mode',
    choices: ['auto', 'recompute', 'require'],
    defaultValue: 'auto',
    placeholder: 'mode',
  },
];

export const TRANSFER_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'get',
    positional: [uri, { name: 'local-path' }],
    skeleton: 'resource',
    execute: ({ uri, 'local-path': destination }, _, execution) =>
      downloadCommandFile(
        execution,
        {
          method: 'GET',
          path: OV_PATHS.contentDownload,
          query: { uri: uri as string },
        },
        destination as string,
      ),
  },
  ...(['export', 'backup'] as const).map((name): CommandSpec => ({
    name,
    admin: name === 'backup',
    positional: name === 'export' ? [uri, output] : [output],
    flags: [vectors],
    skeleton: 'resource',
    execute: ({ 'output.ovpack': destination, ...body }, _, execution) =>
      downloadCommandFile(
        execution,
        {
          method: 'POST',
          path: name === 'export' ? OV_PATHS.packExport : OV_PATHS.packBackup,
          body,
        },
        destination as string,
        '.ovpack',
      ),
  })),
  ...(['import', 'restore'] as const).map((name): CommandSpec => ({
    name,
    admin: name === 'restore',
    positional:
      name === 'import'
        ? [pack, { ...uri, name: 'target-uri', valueKey: 'parent' }]
        : [{ ...pack, name: 'backup.ovpack', valueKey: 'file.ovpack' }],
    flags: importFlags,
    skeleton: 'write',
    execute: async (
      { 'file.ovpack': filename, ...body },
      request,
      execution,
    ) => {
      const temp_file_id = await uploadCommandFile(
        execution,
        filename as string,
        '.ovpack',
        name === 'import' ? (body.parent as string) : undefined,
      );
      const result = await request<{ uri?: string }>({
        method: 'POST',
        path: name === 'import' ? OV_PATHS.packImport : OV_PATHS.packRestore,
        body: { ...body, temp_file_id },
      });
      execution.signal?.throwIfAborted();
      if (name === 'import' && result?.uri) {
        try {
          execution.onPackImported?.(result.uri);
        } catch {
          /* A host notification cannot undo a successful import. */
        }
      }
      return result;
    },
  })),
];
