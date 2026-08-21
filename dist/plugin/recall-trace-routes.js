export const RECALL_TRACE_ROUTE_PATHS = [
    "/api/openviking/recall-traces",
    "/api/openviking/uri-detail",
    "/api/openviking/recall-traces/latest-ov-search-list",
    "/api/openviking/recall-traces/:traceId",
];
export function registerRecallTraceRoutes(ctx, handlers) {
    const routeAdapter = ctx;
    const canRegisterLegacyRoute = typeof routeAdapter?.registerRoute === "function";
    const canRegisterHttpRoute = typeof routeAdapter?.registerHttpRoute === "function";
    if (!canRegisterLegacyRoute && !canRegisterHttpRoute) {
        return false;
    }
    const handle = handlers.handleRecallTraces;
    const routes = [
        { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[0], handler: handle },
        { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[1], handler: handlers.handleUriDetail },
        { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[2], handler: handlers.handleLatestOvSearchList },
        {
            method: "GET",
            path: RECALL_TRACE_ROUTE_PATHS[3],
            handler: (request) => handle({
                ...request,
                query: {
                    ...(request?.query ?? {}),
                    traceId: typeof request?.params?.traceId === "string" ? request.params.traceId : undefined,
                },
            }),
        },
    ];
    for (const route of routes) {
        routeAdapter?.registerRoute?.(route);
    }
    if (canRegisterHttpRoute) {
        const sendJson = (res, status, body) => {
            res.statusCode = status;
            res.setHeader?.("Cache-Control", "no-store");
            res.setHeader?.("Content-Type", "application/json; charset=utf-8");
            res.end?.(JSON.stringify(body));
        };
        const makeHttpHandler = (route, getParams) => async (req, res) => {
            if ((req.method ?? "GET").toUpperCase() !== route.method) {
                sendJson(res, 405, { ok: false, error: { code: "method_not_allowed", message: `${route.method} is required` } });
                return true;
            }
            const url = req.url ?? route.path;
            const result = await route.handler({ url, params: getParams?.(url) });
            const response = result;
            sendJson(res, typeof response.status === "number" ? response.status : 200, response.body ?? response);
            return true;
        };
        for (const route of routes) {
            if (route.path === RECALL_TRACE_ROUTE_PATHS[3]) {
                const prefix = "/api/openviking/recall-traces/";
                routeAdapter?.registerHttpRoute?.({
                    path: "/api/openviking/recall-traces",
                    auth: "plugin",
                    match: "prefix",
                    handler: makeHttpHandler(route, (url) => {
                        const pathname = new URL(url, "http://openclaw.local").pathname;
                        const traceId = pathname.startsWith(prefix) ? decodeURIComponent(pathname.slice(prefix.length)) : "";
                        return traceId ? { traceId } : {};
                    }),
                });
            }
            else {
                routeAdapter?.registerHttpRoute?.({
                    path: route.path,
                    auth: "plugin",
                    match: "exact",
                    handler: makeHttpHandler(route),
                });
            }
        }
    }
    return true;
}
