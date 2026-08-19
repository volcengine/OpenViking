export function withTimeout(promise, timeoutMs, timeoutMessage) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs);
        promise.then((value) => {
            clearTimeout(timer);
            resolve(value);
        }, (err) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}
export async function quickHealthCheck(client, agentId, timeoutMs) {
    try {
        await client.healthCheck(timeoutMs, agentId);
        return true;
    }
    catch {
        return false;
    }
}
export async function quickRecallPrecheck(client, agentId) {
    const healthOk = await quickHealthCheck(client, agentId, 500);
    if (healthOk) {
        return { ok: true };
    }
    return { ok: false, reason: "health check failed" };
}
