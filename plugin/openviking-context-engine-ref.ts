import type { ContextEngineWithCommit } from "../context-engine.js";

export function createOpenVikingContextEngineRef() {
  let contextEngineRef: ContextEngineWithCommit | null = null;

  const getContextEngine = (): ContextEngineWithCommit | null => contextEngineRef;

  const setContextEngineRef = (engine: ContextEngineWithCommit): void => {
    contextEngineRef = engine;
  };

  return {
    getContextEngine,
    setContextEngineRef,
  };
}
