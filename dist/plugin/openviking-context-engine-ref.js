export function createOpenVikingContextEngineRef() {
    let contextEngineRef = null;
    const getContextEngine = () => contextEngineRef;
    const setContextEngineRef = (engine) => {
        contextEngineRef = engine;
    };
    return {
        getContextEngine,
        setContextEngineRef,
    };
}
