export type FilesystemPanel = 'agent' | 'terminal'

export type FilesystemSearch = {
  uri?: string
  file?: string
  panel?: FilesystemPanel
  session?: string
  upload?: boolean
}

export const validateFilesystemSearch = (
  search: Record<string, unknown>,
): FilesystemSearch => ({
  file: typeof search.file === 'string' ? search.file : undefined,
  panel:
    search.panel === 'agent' || search.panel === 'terminal'
      ? search.panel
      : undefined,
  session: typeof search.session === 'string' ? search.session : undefined,
  upload: search.upload === true || search.upload === 'true',
  uri: typeof search.uri === 'string' ? search.uri : undefined,
})

export type ResourceOpenHandler = (uri: string) => Promise<void> | void
