export function resolveOpenVikingRequestHeaders(options = {}) {
    return cleanOpenVikingRequestHeaders(options.headers);
}
export function cleanOpenVikingRequestHeaders(headers) {
    if (headers === undefined || headers === null) {
        return {};
    }
    if (typeof headers !== "object" || Array.isArray(headers)) {
        throw new Error("openviking request headers must be an object");
    }
    const out = {};
    for (const [key, value] of Object.entries(headers)) {
        if (typeof value !== "string") {
            throw new Error(`openviking request header ${key} must be a string`);
        }
        out[key] = value;
    }
    return out;
}
