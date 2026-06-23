import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Type, type Static } from "@sinclair/typebox";
import { Value } from "@sinclair/typebox/value";

export const OPENVIKING_260610_FEATURE = "openviking.260610";
export const OPENVIKING_FEATURE_GATES_RPC = "openviking.feature.gates";

const versionPattern = String.raw`^\d+\.\d+\.\d+(?:-(?:\d+|beta(?:\.\d+)?))?$`;

const FeatureGateSchema = Type.Object(
  {
    enable: Type.Boolean(),
    minPluginVersion: Type.String({ pattern: versionPattern }),
    minOpenclawVersion: Type.String({ minLength: 1 }),
    editions: Type.Array(Type.String({ minLength: 1 }), {
      minItems: 1,
      uniqueItems: true,
    }),
  },
  { additionalProperties: false },
);

const FeatureGatesConfigSchema = Type.Object(
  {
    features: Type.Record(Type.String({ minLength: 1 }), FeatureGateSchema),
  },
  { additionalProperties: false },
);

type FeatureGatesConfig = Static<typeof FeatureGatesConfigSchema>;

interface ParsedVersion {
  year: number;
  month: number;
  day: number;
  channelRank: number;
  sequence: number;
}

export interface OpenVikingFeatureGateServiceOptions {
  configPath?: string;
  getPluginVersion?: () => string;
  getOpenClawVersion?: () => Promise<string>;
  getOpenClawVersionSync?: () => string;
}

export interface OpenVikingFeatureGateService {
  getEnabledFeatureGates(edition?: string): Promise<string[]>;
  isFeatureGateEnabled(featureName: string, edition?: string): Promise<boolean>;
  isFeatureGateEnabledSync(featureName: string, edition?: string): boolean;
}

export interface OpenVikingFeatureGatesGatewayApi {
  registerGatewayMethod?: (
    name: string,
    handler: (input: {
      params?: unknown;
      respond: (success: boolean, data: unknown) => void;
    }) => void | Promise<void>,
  ) => void;
}

function parseVersion(version: string): ParsedVersion | null {
  const stableMatch = version.match(/^(\d+)\.(\d+)\.(\d+)(?:-(\d+))?$/);
  if (stableMatch) {
    return {
      year: Number(stableMatch[1]),
      month: Number(stableMatch[2]),
      day: Number(stableMatch[3]),
      channelRank: 1,
      sequence: stableMatch[4] ? Number(stableMatch[4]) : 0,
    };
  }

  const betaMatch = version.match(/^(\d+)\.(\d+)\.(\d+)-beta(?:\.(\d+))?$/);
  if (betaMatch) {
    return {
      year: Number(betaMatch[1]),
      month: Number(betaMatch[2]),
      day: Number(betaMatch[3]),
      channelRank: 0,
      sequence: betaMatch[4] ? Number(betaMatch[4]) : 0,
    };
  }

  return null;
}

export function isPluginVersionAtLeast(currentVersion: string, minVersion: string): boolean {
  const current = parseVersion(currentVersion);
  const minimum = parseVersion(minVersion);
  if (!current || !minimum) {
    return false;
  }

  if (current.year !== minimum.year) return current.year > minimum.year;
  if (current.month !== minimum.month) return current.month > minimum.month;
  if (current.day !== minimum.day) return current.day > minimum.day;
  if (current.channelRank !== minimum.channelRank) {
    return current.channelRank > minimum.channelRank;
  }
  return current.sequence >= minimum.sequence;
}

export function isOpenClawVersionAtLeast(currentVersion: string, minVersion: string): boolean {
  return isPluginVersionAtLeast(currentVersion, minVersion);
}

function formatSchemaError(path: string, message: string): string {
  const normalizedPath = path ? path.replace(/\//g, ".").replace(/^\./, "") : "$";
  return `${normalizedPath}: ${message}`;
}

function parseFeatureGatesConfig(raw: string): FeatureGatesConfig {
  const parsed = JSON.parse(raw) as unknown;
  if (Value.Check(FeatureGatesConfigSchema, parsed)) {
    return parsed;
  }

  const validationError = Value.Errors(FeatureGatesConfigSchema, parsed).First();
  const errorMessage = validationError
    ? formatSchemaError(validationError.path, validationError.message)
    : "unknown validation error";
  throw new Error(`Invalid feature-gates.json: ${errorMessage}`);
}

function normalizeFeatures(
  features: FeatureGatesConfig["features"],
  pluginVersion: string,
  openclawVersion: string,
  edition?: string,
): string[] {
  const requestedEdition = edition?.trim();
  const businessCarriers = (process.env.BUSINESS_CARRIER ?? "")
    .split(",")
    .map((carrier) => carrier.trim())
    .filter(Boolean);
  const effectiveEditions = requestedEdition ? [requestedEdition] : businessCarriers;

  return Object.entries(features).flatMap(([featureName, featureConfig]) => {
    const editionOk =
      effectiveEditions.length > 0 &&
      effectiveEditions.some((carrier) => featureConfig.editions.includes(carrier));

    if (
      featureConfig.enable &&
      editionOk &&
      isPluginVersionAtLeast(pluginVersion, featureConfig.minPluginVersion) &&
      isOpenClawVersionAtLeast(openclawVersion, featureConfig.minOpenclawVersion)
    ) {
      return [featureName];
    }
    return [];
  });
}

let cachedPackageRoot: string | undefined;
let cachedDefaultPluginVersion: string | undefined;

function findPackageRoot(startDir: string): string {
  let current = startDir;
  for (;;) {
    if (existsSync(join(current, "package.json"))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) {
      return startDir;
    }
    current = parent;
  }
}

function getPackageRoot(): string {
  cachedPackageRoot ??= findPackageRoot(dirname(fileURLToPath(import.meta.url)));
  return cachedPackageRoot;
}

function getDefaultFeatureGatesPath(): string {
  return join(getPackageRoot(), "config", "feature-gates.json");
}

function getDefaultPluginVersion(): string {
  if (cachedDefaultPluginVersion) {
    return cachedDefaultPluginVersion;
  }
  try {
    const raw = readFileSync(join(getPackageRoot(), "package.json"), "utf8");
    const parsed = JSON.parse(raw) as { version?: unknown };
    cachedDefaultPluginVersion = typeof parsed.version === "string" ? parsed.version : "0.0.0";
  } catch {
    cachedDefaultPluginVersion = "0.0.0";
  }
  return cachedDefaultPluginVersion;
}

function getDefaultOpenClawVersionSync(): string {
  const envVersion = process.env.OPENCLAW_VERSION?.trim();
  return envVersion || "2026.4.8";
}

export function createOpenVikingFeatureGateService(
  options: OpenVikingFeatureGateServiceOptions = {},
): OpenVikingFeatureGateService {
  const configPath = options.configPath ?? getDefaultFeatureGatesPath();
  const getPluginVersion = options.getPluginVersion ?? getDefaultPluginVersion;
  const getOpenClawVersion = options.getOpenClawVersion ?? (async () => getDefaultOpenClawVersionSync());
  const getOpenClawVersionSync = options.getOpenClawVersionSync ?? getDefaultOpenClawVersionSync;
  let cachedConfig: FeatureGatesConfig | undefined;
  let cachedConfigPromise: Promise<FeatureGatesConfig> | undefined;
  let cachedOpenClawVersion: string | undefined;
  let cachedOpenClawVersionPromise: Promise<string> | undefined;

  async function loadFeatureGatesConfig(): Promise<FeatureGatesConfig> {
    if (cachedConfig) {
      return cachedConfig;
    }
    cachedConfigPromise ??= readFile(configPath, "utf8")
      .then((raw) => {
        cachedConfig = parseFeatureGatesConfig(raw);
        return cachedConfig;
      })
      .catch((error: unknown) => {
        cachedConfigPromise = undefined;
        throw error;
      });
    return cachedConfigPromise;
  }

  function loadFeatureGatesConfigSync(): FeatureGatesConfig {
    if (cachedConfig) {
      return cachedConfig;
    }
    const raw = readFileSync(configPath, "utf8");
    cachedConfig = parseFeatureGatesConfig(raw);
    return cachedConfig;
  }

  async function loadOpenClawVersion(): Promise<string> {
    if (cachedOpenClawVersion) {
      return cachedOpenClawVersion;
    }
    cachedOpenClawVersionPromise ??= getOpenClawVersion()
      .then((version) => {
        cachedOpenClawVersion = version;
        return version;
      })
      .catch((error: unknown) => {
        cachedOpenClawVersionPromise = undefined;
        throw error;
      });
    return cachedOpenClawVersionPromise;
  }

  function loadOpenClawVersionSync(): string {
    cachedOpenClawVersion ??= getOpenClawVersionSync();
    return cachedOpenClawVersion;
  }

  function loadAndNormalizeFeaturesSync(edition?: string): string[] {
    const parsed = loadFeatureGatesConfigSync();
    return normalizeFeatures(parsed.features, getPluginVersion(), loadOpenClawVersionSync(), edition);
  }

  async function loadAndNormalizeFeatures(edition?: string): Promise<string[]> {
    const [parsed, openclawVersion] = await Promise.all([
      loadFeatureGatesConfig(),
      loadOpenClawVersion(),
    ]);
    return normalizeFeatures(parsed.features, getPluginVersion(), openclawVersion, edition);
  }

  return {
    async getEnabledFeatureGates(edition?: string): Promise<string[]> {
      return loadAndNormalizeFeatures(edition);
    },

    async isFeatureGateEnabled(featureName: string, edition?: string): Promise<boolean> {
      try {
        const features = await loadAndNormalizeFeatures(edition);
        return features.includes(featureName);
      } catch {
        return false;
      }
    },

    isFeatureGateEnabledSync(featureName: string, edition?: string): boolean {
      try {
        const features = loadAndNormalizeFeaturesSync(edition);
        return features.includes(featureName);
      } catch {
        return false;
      }
    },
  };
}

function readEditionParam(params: unknown): string | undefined {
  if (!params || typeof params !== "object") {
    return undefined;
  }
  const value = (params as { edition?: unknown }).edition;
  return typeof value === "string" ? value : undefined;
}

const defaultFeatureGateService = createOpenVikingFeatureGateService();

export const getEnabledFeatureGates = (edition?: string): Promise<string[]> =>
  defaultFeatureGateService.getEnabledFeatureGates(edition);
export const isFeatureGateEnabled = (featureName: string, edition?: string): Promise<boolean> =>
  defaultFeatureGateService.isFeatureGateEnabled(featureName, edition);
export const isFeatureGateEnabledSync = (featureName: string, edition?: string): boolean =>
  defaultFeatureGateService.isFeatureGateEnabledSync(featureName, edition);

export function registerOpenVikingFeatureGatesMethod(
  api: OpenVikingFeatureGatesGatewayApi,
  service: OpenVikingFeatureGateService = defaultFeatureGateService,
): void {
  if (typeof api.registerGatewayMethod !== "function") {
    return;
  }

  api.registerGatewayMethod(OPENVIKING_FEATURE_GATES_RPC, async ({ params, respond }) => {
    try {
      const edition = readEditionParam(params);
      const features = await service.getEnabledFeatureGates(edition);
      respond(true, { features });
    } catch (error) {
      respond(false, error instanceof Error ? error.message : String(error));
    }
  });
}
