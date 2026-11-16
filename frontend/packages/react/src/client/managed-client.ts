import { createOvClient } from './client.js';
import { resolveIdentityLayer, toHeaders } from './options.js';
import type { QueryScope } from './query-keys.js';
import type {
  RequestConcurrency,
  RequestScheduler,
} from './request-scheduler.js';
import { OV_PATHS, type OvPath } from './paths.js';
const directoryWrites = new Set<string>([
  OV_PATHS.fsMkdir,
  OV_PATHS.fsMv,
  OV_PATHS.fsCp,
  OV_PATHS.fsRm,
  OV_PATHS.packImport,
  OV_PATHS.contentWrite,
  OV_PATHS.resources,
  OV_PATHS.skills,
]);
import type {
  OvClient,
  OvClientOptions,
  OvRequestOptions,
  OvRequestSpec,
} from './types.js';

interface Configuration {
  options: OvClientOptions;
  scheduler: RequestScheduler;
  concurrency: RequestConcurrency;
}
const configurations = new WeakMap<OvClient, Configuration>();

/** Transport decorators retain the same identity, scheduler and cache scope. */
export function inheritClientConfiguration(
  client: OvClient,
  decorated: OvClient,
): OvClient {
  const configuration = configurations.get(client);
  if (configuration) configurations.set(decorated, configuration);
  return decorated;
}

function serviceURL(baseURL = '') {
  return new URL(
    baseURL.replace(/\/+$/, '') || '/',
    globalThis.location?.href ?? 'http://localhost/',
  ).href.replace(/\/$/, '');
}

export async function resolveQueryRequest(
  client: OvClient,
  local: OvRequestOptions | undefined,
  scheduler: RequestScheduler,
) {
  const configuration = configurations.get(client);
  const defaults = configuration?.options ?? { baseURL: client.baseURL };
  const options = { ...defaults };
  for (const [key, value] of Object.entries(local ?? {}) as [
    keyof OvRequestOptions,
    OvRequestOptions[keyof OvRequestOptions],
  ][])
    if (value !== undefined) Object.assign(options, { [key]: value });
  const identity = await resolveIdentityLayer(options);
  Object.assign(options, identity);
  const scope: QueryScope = {
    service: serviceURL(options.baseURL),
    credentials: options.credentials ?? 'include',
    identity: scheduler.identity(JSON.stringify(toHeaders(identity))),
    transport: scheduler.identity(
      String(transportId(options.fetch ?? globalThis.fetch)),
    ),
  };
  return {
    scope,
    client: configuration
      ? createManagedClient(
          options,
          configuration.scheduler,
          configuration.concurrency,
        )
      : client,
    options: { ...local, ...identity, baseURL: options.baseURL },
    signal: options.signal,
  };
}

const transports = new WeakMap<object, number>();
let nextTransport = 0;
function transportId(fetch: typeof globalThis.fetch) {
  let id = transports.get(fetch);
  if (id === undefined) {
    id = ++nextTransport;
    transports.set(fetch, id);
  }
  return id;
}

/** Resolve identity once before queueing, and keep parsing inside the request slot. */
export function createManagedClient(
  options: OvClientOptions,
  scheduler: RequestScheduler,
  concurrency: RequestConcurrency,
): OvClient {
  const execute = async <T>(
    spec: OvRequestSpec,
    run: (client: OvClient, spec: OvRequestSpec) => Promise<T>,
  ) => {
    const prepared = await resolveQueryRequest(client, spec.options, scheduler);
    const raw = createOvClient({ ...options, ...prepared.options });
    const key = JSON.stringify([prepared.scope, spec.path]);
    const result = await scheduler.run(
      key,
      concurrency[spec.path as OvPath],
      () => run(raw, { ...spec, options: prepared.options }),
      prepared.signal,
    );
    if (
      spec.method !== 'GET' &&
      spec.method !== 'HEAD' &&
      directoryWrites.has(spec.path)
    )
      scheduler.invalidate(prepared.scope);
    return result;
  };
  const client: OvClient = {
    baseURL: options.baseURL ?? '',
    request: (spec) => execute(spec, (raw, request) => raw.request(request)),
    requestEnvelope: (spec) =>
      execute(spec, (raw, request) => raw.requestEnvelope(request)),
    stream: (spec) =>
      execute(spec, (raw, request) =>
        raw.stream({ ...spec, options: request.options }),
      ),
  };
  configurations.set(client, { options, scheduler, concurrency });
  return client;
}
