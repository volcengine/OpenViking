import {
  hasCompleteOvpack,
  hasOvpackHeader,
  hasOvpackName,
} from '../../shared/ovpack-file.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvRequestSpec } from '../../client/types.js';
import { ConsoleCommandError } from './command-messages.js';
import type { ConsoleCommandFiles, ConsoleCommandOptions } from './types.js';

export interface CommandExecution {
  request: <T = Record<string, unknown>>(spec: OvRequestSpec) => Promise<T>;
  files: ConsoleCommandFiles;
  choosePackFile?: ConsoleCommandOptions['choosePackFile'];
  onPackImported?: ConsoleCommandOptions['onPackImported'];
  signal?: AbortSignal;
}

export const browserCommandFiles: ConsoleCommandFiles = {
  pickFile({ accept, signal }) {
    signal?.throwIfAborted();
    return new Promise((resolve, reject) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.hidden = true;
      if (accept) input.accept = accept;
      function cleanup() {
        signal?.removeEventListener('abort', abort);
        input.remove();
      }
      function abort() {
        cleanup();
        reject(new ConsoleCommandError('cancelled'));
      }
      function finish() {
        const file = input.files?.[0];
        cleanup();
        resolve(file);
      }
      input.addEventListener('change', finish, { once: true });
      input.addEventListener('cancel', finish, { once: true });
      signal?.addEventListener('abort', abort, { once: true });
      document.body.append(input);
      input.click();
    });
  },
  download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};

export async function selectCommandFile(
  execution: CommandExecution,
  name: string,
  accept?: string,
) {
  const file = await execution.files.pickFile({
    name,
    accept,
    signal: execution.signal,
  });
  execution.signal?.throwIfAborted();
  if (!file) throw new ConsoleCommandError('cancelled');
  return file;
}

export async function uploadCommandFile(
  execution: CommandExecution,
  name: string,
  accept?: string,
  targetUri?: string,
) {
  const file =
    targetUri && execution.choosePackFile
      ? await execution.choosePackFile(targetUri, execution.signal)
      : await selectCommandFile(execution, name, accept);
  if (!file) throw new ConsoleCommandError('cancelled');
  if (
    accept === '.ovpack' &&
    (!hasOvpackName(file.name) || !(await hasOvpackHeader(file)))
  )
    throw new ConsoleCommandError('invalidOvpack');
  const body = new FormData();
  body.append('file', file);
  const result = await execution.request<{ temp_file_id?: string }>({
    method: 'POST',
    path: OV_PATHS.resourceTempUpload,
    body,
  });
  if (!result?.temp_file_id) throw new ConsoleCommandError('missingUploadId');
  return result.temp_file_id;
}

export async function downloadCommandFile(
  execution: CommandExecution,
  spec: OvRequestSpec,
  destination: string,
  extension = '',
) {
  const name = destination.split(/[\\/]/).pop();
  if (!name || name === '.' || name === '..')
    throw new ConsoleCommandError('invalidArgument', destination);
  const filename = name.endsWith(extension) ? name : name + extension;
  const blob = await execution.request<Blob>({ ...spec, responseType: 'blob' });
  execution.signal?.throwIfAborted();
  if (extension === '.ovpack' && !(await hasCompleteOvpack(blob)))
    throw new ConsoleCommandError('invalidOvpackDownload');
  execution.signal?.throwIfAborted();
  await execution.files.download(blob, filename);
  return { filename };
}
