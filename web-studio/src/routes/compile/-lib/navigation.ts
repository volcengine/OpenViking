export type CompileListOrigin = {
  scope: string
  index: number
  search: { status?: string; q?: string }
}

declare module '@tanstack/react-router' {
  interface HistoryState {
    compileListOrigin?: CompileListOrigin
  }
}

export function compileListReturn(
  origin: CompileListOrigin | undefined,
  scope: string,
  currentIndex: number,
) {
  const sameIdentity = origin?.scope === scope
  return {
    search: sameIdentity ? origin.search : {},
    restoreHistory: sameIdentity && currentIndex === origin.index + 1,
  }
}
