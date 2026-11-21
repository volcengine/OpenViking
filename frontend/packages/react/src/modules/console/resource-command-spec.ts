import { OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import type { Argument } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { uploadCommandFile } from './command-files.js';
import {
  processingMode,
  tags,
  tagMode,
  tagFields,
} from './content-command-specs.js';

export const tempFileId: Argument = {
  name: 'temp-file-id',
  valueKey: 'temp_file_id',
  placeholder: 'id',
  pattern: /\S/,
};
export const telemetry: Argument = {
  name: 'telemetry',
  kind: 'boolean-or-json',
  placeholder: 'true|json',
};

export function isRemoteResourceSource(path: unknown, addType: unknown) {
  return (
    typeof path === 'string' &&
    (Boolean(addType) ||
      path.startsWith('git@') ||
      /^[a-z][a-z\d+.-]*:\/\//i.test(path))
  );
}

export const RESOURCE_COMMAND_SPEC: CommandSpec = {
  name: 'add-resource',
  positional: [
    {
      name: 'path',
      descriptionKey: 'resource-source',
      placeholder: 'path-or-url',
      pattern: /\S/,
      optional: true,
    },
  ],
  flags: [
    tempFileId,
    { name: 'add-type', valueKey: 'add_type', placeholder: 'type' },
    processingMode,
    tags,
    tagMode,
    { name: 'no-directly-upload-media', kind: 'boolean' },
    { name: 'to', kind: 'uri' },
    { name: 'parent', kind: 'uri' },
    { name: 'parent-auto-create', kind: 'uri', alias: '-p' },
    {
      name: 'create-parent',
      valueKey: 'create_parent',
      kind: 'boolean',
      hidden: true,
    },
    { name: 'reason', placeholder: 'text' },
    {
      name: 'instruction',
      placeholder: 'text',
      descriptionKey: 'resource-instruction',
    },
    { name: 'ignore-dirs', valueKey: 'ignore_dirs', placeholder: 'names' },
    { name: 'include', placeholder: 'patterns' },
    { name: 'exclude', placeholder: 'patterns' },
    { name: 'strict', kind: 'boolean', hidden: true },
    {
      name: 'directly-upload-media',
      valueKey: 'directly_upload_media',
      kind: 'boolean',
      hidden: true,
    },
    {
      name: 'preserve-structure',
      valueKey: 'preserve_structure',
      kind: 'boolean',
      hidden: true,
    },
    { ...telemetry, hidden: true },
    {
      name: 'watch-interval',
      valueKey: 'watch_interval',
      kind: 'number',
      min: 0,
      placeholder: 'minutes',
      helpDefault: 0,
    },
    { name: 'args', kind: 'json-object', placeholder: 'json-object' },
    {
      name: 'acl',
      descriptionKey: 'resource-acl',
      kind: 'json-object',
      placeholder: 'json-object',
    },
    { name: 'wait', kind: 'boolean' },
  ],
  skeleton: 'write',
  execute: async (
    {
      'parent-auto-create': autoParent,
      'no-directly-upload-media': noDirectUpload,
      path,
      temp_file_id: uploadId,
      tags,
      tag_mode,
      ...body
    },
    request,
    execution,
  ) => {
    if (path && uploadId)
      throw new ConsoleCommandError(
        'invalidArgument',
        '<path-or-url> / --temp-file-id',
      );
    if (
      body.add_type &&
      (!path || !body.to || body.parent || autoParent || uploadId)
    )
      throw new ConsoleCommandError(
        'invalidArgument',
        '--add-type requires a source and --to',
      );
    if ([body.to, body.parent, autoParent].filter(Boolean).length > 1)
      throw new ConsoleCommandError('invalidArgument', '--to / --parent / -p');
    const source = isRemoteResourceSource(path, body.add_type)
      ? { path }
      : {
          temp_file_id:
            uploadId ??
            (await uploadCommandFile(
              execution,
              typeof path === 'string' ? path : '',
            )),
        };
    return request({
      method: 'POST',
      path: OV_PATHS.resources,
      body: {
        ...body,
        ...source,
        ...tagFields(tags, tag_mode),
        ...(noDirectUpload === true ? { directly_upload_media: false } : {}),
        ...(autoParent ? { parent: autoParent, create_parent: true } : {}),
      },
    });
  },
};
