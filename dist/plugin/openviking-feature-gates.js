import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "@sinclair/typebox";
import { Value } from "@sinclair/typebox/value";
export const OPENVIKING_260610_FEATURE = "openviking.260610";
export const OPENVIKING_FEATURE_GATES_RPC = "openviking.feature.gates";
const versionPattern = String.raw `^\d+\.\d+\.\d+(?:-(?:\d+|beta(?:\.\d+)?))?$`;
const FeatureGateSchema = Type.Object({
    enable: Type.Boolean(),
    minPluginVersion: Type.String({ pattern: versionPattern }),
    minOpenclawVersion: Type.String({ minLength: 1 }),
    editions: Type.Array(Type.String({ minLength: 1 }), {
        minItems: 1,
        uniqueItems: true,
    }),
}, { additionalProperties: false });
const FeatureGatesConfigSchema = Type.Object({
    features: Type.Record(Type.String({ minLength: 1 }), FeatureGateSchema),
}, { additionalProperties: false });
function parseVersion(version) {
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
export function isPluginVersionAtLeast(currentVersion, minVersion) {
    const current = parseVersion(currentVersion);
    const minimum = parseVersion(minVersion);
    if (!current || !minimum) {
        return false;
    }
    if (current.year !== minimum.year)
        return current.year > minimum.year;
    if (current.month !== minimum.month)
        return current.month > minimum.month;
    if (current.day !== minimum.day)
        return current.day > minimum.day;
    if (current.channelRank !== minimum.channelRank) {
        return current.channelRank > minimum.channelRank;
    }
    return current.sequence >= minimum.sequence;
}
export function isOpenClawVersionAtLeast(currentVersion, minVersion) {
    return isPluginVersionAtLeast(currentVersion, minVersion);
}
function formatSchemaError(path, message) {
    const normalizedPath = path ? path.replace(/\//g, ".").replace(/^\./, "") : "$";
    return `${normalizedPath}: ${message}`;
}
function parseFeatureGatesConfig(raw) {
    const parsed = JSON.parse(raw);
    if (Value.Check(FeatureGatesConfigSchema, parsed)) {
        return parsed;
    }
    const validationError = Value.Errors(FeatureGatesConfigSchema, parsed).First();
    const errorMessage = validationError
        ? formatSchemaError(validationError.path, validationError.message)
        : "unknown validation error";
    throw new Error(`Invalid feature-gates.json: ${errorMessage}`);
}
function normalizeFeatures(features, pluginVersion, openclawVersion, edition) {
    const requestedEdition = edition?.trim();
    const businessCarriers = (process.env.BUSINESS_CARRIER ?? "")
        .split(",")
        .map((carrier) => carrier.trim())
        .filter(Boolean);
    const effectiveEditions = requestedEdition ? [requestedEdition] : businessCarriers;
    return Object.entries(features).flatMap(([featureName, featureConfig]) => {
        const editionOk = effectiveEditions.length > 0 &&
            effectiveEditions.some((carrier) => featureConfig.editions.includes(carrier));
        if (featureConfig.enable &&
            editionOk &&
            isPluginVersionAtLeast(pluginVersion, featureConfig.minPluginVersion) &&
            isOpenClawVersionAtLeast(openclawVersion, featureConfig.minOpenclawVersion)) {
            return [featureName];
        }
        return [];
    });
}
let cachedPackageRoot;
let cachedDefaultPluginVersion;
function findPackageRoot(startDir) {
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
function getPackageRoot() {
    cachedPackageRoot ??= findPackageRoot(dirname(fileURLToPath(import.meta.url)));
    return cachedPackageRoot;
}
function getDefaultFeatureGatesPath() {
    return join(getPackageRoot(), "config", "feature-gates.json");
}
function getDefaultPluginVersion() {
    if (cachedDefaultPluginVersion) {
        return cachedDefaultPluginVersion;
    }
    try {
        const raw = readFileSync(join(getPackageRoot(), "package.json"), "utf8");
        const parsed = JSON.parse(raw);
        cachedDefaultPluginVersion = typeof parsed.version === "string" ? parsed.version : "0.0.0";
    }
    catch {
        cachedDefaultPluginVersion = "0.0.0";
    }
    return cachedDefaultPluginVersion;
}
function getDefaultOpenClawVersionSync() {
    const envVersion = process.env.OPENCLAW_VERSION?.trim();
    return envVersion || "2026.4.8";
}
export function createOpenVikingFeatureGateService(options = {}) {
    const configPath = options.configPath ?? getDefaultFeatureGatesPath();
    const getPluginVersion = options.getPluginVersion ?? getDefaultPluginVersion;
    const getOpenClawVersion = options.getOpenClawVersion ?? (async () => getDefaultOpenClawVersionSync());
    const getOpenClawVersionSync = options.getOpenClawVersionSync ?? getDefaultOpenClawVersionSync;
    let cachedConfig;
    let cachedConfigPromise;
    let cachedOpenClawVersion;
    let cachedOpenClawVersionPromise;
    async function loadFeatureGatesConfig() {
        if (cachedConfig) {
            return cachedConfig;
        }
        cachedConfigPromise ??= readFile(configPath, "utf8")
            .then((raw) => {
            cachedConfig = parseFeatureGatesConfig(raw);
            return cachedConfig;
        })
            .catch((error) => {
            cachedConfigPromise = undefined;
            throw error;
        });
        return cachedConfigPromise;
    }
    function loadFeatureGatesConfigSync() {
        if (cachedConfig) {
            return cachedConfig;
        }
        const raw = readFileSync(configPath, "utf8");
        cachedConfig = parseFeatureGatesConfig(raw);
        return cachedConfig;
    }
    async function loadOpenClawVersion() {
        if (cachedOpenClawVersion) {
            return cachedOpenClawVersion;
        }
        cachedOpenClawVersionPromise ??= getOpenClawVersion()
            .then((version) => {
            cachedOpenClawVersion = version;
            return version;
        })
            .catch((error) => {
            cachedOpenClawVersionPromise = undefined;
            throw error;
        });
        return cachedOpenClawVersionPromise;
    }
    function loadOpenClawVersionSync() {
        cachedOpenClawVersion ??= getOpenClawVersionSync();
        return cachedOpenClawVersion;
    }
    function loadAndNormalizeFeaturesSync(edition) {
        const parsed = loadFeatureGatesConfigSync();
        return normalizeFeatures(parsed.features, getPluginVersion(), loadOpenClawVersionSync(), edition);
    }
    async function loadAndNormalizeFeatures(edition) {
        const [parsed, openclawVersion] = await Promise.all([
            loadFeatureGatesConfig(),
            loadOpenClawVersion(),
        ]);
        return normalizeFeatures(parsed.features, getPluginVersion(), openclawVersion, edition);
    }
    return {
        async getEnabledFeatureGates(edition) {
            return loadAndNormalizeFeatures(edition);
        },
        async isFeatureGateEnabled(featureName, edition) {
            try {
                const features = await loadAndNormalizeFeatures(edition);
                return features.includes(featureName);
            }
            catch {
                return false;
            }
        },
        isFeatureGateEnabledSync(featureName, edition) {
            try {
                const features = loadAndNormalizeFeaturesSync(edition);
                return features.includes(featureName);
            }
            catch {
                return false;
            }
        },
    };
}
function readEditionParam(params) {
    if (!params || typeof params !== "object") {
        return undefined;
    }
    const value = params.edition;
    return typeof value === "string" ? value : undefined;
}
const defaultFeatureGateService = createOpenVikingFeatureGateService();
export const getEnabledFeatureGates = (edition) => defaultFeatureGateService.getEnabledFeatureGates(edition);
export const isFeatureGateEnabled = (featureName, edition) => defaultFeatureGateService.isFeatureGateEnabled(featureName, edition);
export const isFeatureGateEnabledSync = (featureName, edition) => defaultFeatureGateService.isFeatureGateEnabledSync(featureName, edition);
export function registerOpenVikingFeatureGatesMethod(api, service = defaultFeatureGateService) {
    if (typeof api.registerGatewayMethod !== "function") {
        return;
    }
    api.registerGatewayMethod(OPENVIKING_FEATURE_GATES_RPC, async ({ params, respond }) => {
        try {
            const edition = readEditionParam(params);
            const features = await service.getEnabledFeatureGates(edition);
            respond(true, { features });
        }
        catch (error) {
            respond(false, error instanceof Error ? error.message : String(error));
        }
    });
}
