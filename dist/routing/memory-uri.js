const MEMORY_URI_PATTERNS = [
    /^viking:\/\/user\/(?:[^/]+(?:\/agent\/[^/]+)?\/)?memories(?:\/|$)/,
    /^viking:\/\/user\/[^/]+\/peers\/[^/]+\/memories(?:\/|$)/,
    // viking://~ is the home alias for the caller's own user space. Configured targets and
    // model-supplied URIs may use it; server responses stay canonical viking://user/<uid>/...
    /^viking:\/\/~\/memories(?:\/|$)/,
    /^viking:\/\/~\/peers\/[^/]+\/memories(?:\/|$)/,
    /^viking:\/\/agent\/(?:[^/]+(?:\/user\/[^/]+)?\/)?memories(?:\/|$)/,
];
export function isMemoryUri(uri) {
    return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}
