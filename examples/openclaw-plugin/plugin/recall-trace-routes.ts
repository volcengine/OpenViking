export type RecallTraceRouteRequest = {
  query?: Record<string, unknown>;
  params?: Record<string, unknown>;
  url?: string;
};

export type RecallTraceRouteResult = {
  status?: number;
  body?: unknown;
};

export type RecallTraceRouteHandler = (request?: RecallTraceRouteRequest) => Promise<RecallTraceRouteResult | unknown>;

export type RecallTraceRoute = {
  method: "GET";
  path: string;
  handler: RecallTraceRouteHandler;
};

export type RecallTraceRouteAdapter = {
  registerRoute?: (route: RecallTraceRoute) => void;
  registerHttpRoute?: (route: {
    path: string;
    auth: "plugin";
    match: "exact" | "prefix";
    handler: (req: { method?: string; url?: string }, res: {
      statusCode?: number;
      setHeader?: (name: string, value: string) => void;
      end?: (body?: string) => void;
    }) => Promise<boolean>;
  }) => void;
};

export type RecallTraceRouteHandlers = {
  handleRecallTraces: RecallTraceRouteHandler;
  handleUriDetail: RecallTraceRouteHandler;
  handleLatestOvSearchList: RecallTraceRouteHandler;
};

export const RECALL_TRACE_ROUTE_PATHS = [
  "/api/openviking/recall-traces",
  "/api/openviking/uri-detail",
  "/api/openviking/recall-traces/latest-ov-search-list",
  "/api/openviking/recall-traces/:traceId",
] as const;

export function registerRecallTraceRoutes(
  ctx: unknown,
  handlers: RecallTraceRouteHandlers,
): boolean {
  const routeAdapter = ctx as RecallTraceRouteAdapter | undefined;
  const canRegisterLegacyRoute = typeof routeAdapter?.registerRoute === "function";
  const canRegisterHttpRoute = typeof routeAdapter?.registerHttpRoute === "function";
  if (!canRegisterLegacyRoute && !canRegisterHttpRoute) {
    return false;
  }

  const handle = handlers.handleRecallTraces;
  const routes: RecallTraceRoute[] = [
    { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[0], handler: handle },
    { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[1], handler: handlers.handleUriDetail },
    { method: "GET", path: RECALL_TRACE_ROUTE_PATHS[2], handler: handlers.handleLatestOvSearchList },
    {
      method: "GET",
      path: RECALL_TRACE_ROUTE_PATHS[3],
      handler: (request?: RecallTraceRouteRequest) => handle({
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
    const sendJson = (
      res: { statusCode?: number; setHeader?: (name: string, value: string) => void; end?: (body?: string) => void },
      status: number,
      body: unknown,
    ) => {
      res.statusCode = status;
      res.setHeader?.("Cache-Control", "no-store");
      res.setHeader?.("Content-Type", "application/json; charset=utf-8");
      res.end?.(JSON.stringify(body));
    };
    const makeHttpHandler = (
      route: RecallTraceRoute,
      getParams?: (url: string) => Record<string, unknown>,
    ) => async (
      req: { method?: string; url?: string },
      res: { statusCode?: number; setHeader?: (name: string, value: string) => void; end?: (body?: string) => void },
    ) => {
      if ((req.method ?? "GET").toUpperCase() !== route.method) {
        sendJson(res, 405, { ok: false, error: { code: "method_not_allowed", message: `${route.method} is required` } });
        return true;
      }
      const url = req.url ?? route.path;
      const result = await route.handler({ url, params: getParams?.(url) });
      const response = result as { status?: number; body?: unknown };
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
      } else {
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
