function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function normalizeTargetMap(rawTargets, fieldName) {
  if (rawTargets === undefined) return {};
  if (!isPlainObject(rawTargets)) {
    throw new Error(`${fieldName} must be an object of numeric scales`);
  }

  const targets = {};
  for (const [target, value] of Object.entries(rawTargets)) {
    const scale = Number(value);
    if (!Number.isFinite(scale)) {
      throw new Error(`${fieldName}.${target} must be a finite number`);
    }
    targets[target] = scale;
  }
  return targets;
}

function normalizeCoefficientEntry(name, rawEntry = {}) {
  const entry = isPlainObject(rawEntry) ? rawEntry : {};
  const min = normalizeNumber(entry.min, 0);
  const max = normalizeNumber(entry.max, 1);
  const normalizedMin = Math.min(min, max);
  const normalizedMax = Math.max(min, max);

  return {
    name,
    group: typeof entry.group === 'string' && entry.group.trim()
      ? entry.group.trim()
      : 'face',
    min: normalizedMin,
    max: normalizedMax,
    step: Math.max(0.001, normalizeNumber(entry.step, 0.01)),
    forwardToRobot: entry.forwardToRobot !== false,
    targets: normalizeTargetMap(entry.targets, `coefficients.${name}.targets`),
    positiveTargets: normalizeTargetMap(
      entry.positiveTargets,
      `coefficients.${name}.positiveTargets`,
    ),
    negativeTargets: normalizeTargetMap(
      entry.negativeTargets,
      `coefficients.${name}.negativeTargets`,
    ),
  };
}

function clampAvatarValue(value) {
  return clamp(value, -1, 1);
}

function applyScaledTargets(result, targets, value) {
  for (const [target, scale] of Object.entries(targets)) {
    result[target] = clampAvatarValue((result[target] || 0) + value * scale);
  }
}

function normalizeReverseEntry(name, rawEntry = {}) {
  return normalizeTargetMap(rawEntry, `mappings.${name}`);
}

function clampCanonicalValue(name, value, catalog) {
  const entry = catalog?.byName?.[name];
  if (!entry) return value;
  return clamp(value, entry.min, entry.max);
}

function resolveCanonicalAlias(name, catalog) {
  if (typeof name !== 'string' || !name) return null;
  if (catalog?.byName?.[name]) return name;

  const pascalName = `${name[0].toUpperCase()}${name.slice(1)}`;
  if (catalog?.byName?.[pascalName]) return pascalName;

  return null;
}

export function normalizeCoefficientCatalog(rawConfig = {}) {
  const rawCoefficients = isPlainObject(rawConfig.coefficients)
    ? rawConfig.coefficients
    : rawConfig;

  const coefficients = Object.entries(rawCoefficients).map(([name, rawEntry]) =>
    normalizeCoefficientEntry(name, rawEntry));

  return {
    coefficients,
    byName: Object.fromEntries(coefficients.map((entry) => [entry.name, entry])),
    robotNames: new Set(
      coefficients
        .filter((entry) => entry.forwardToRobot)
        .map((entry) => entry.name),
    ),
  };
}

export function normalizeReverseMapping(rawConfig = {}) {
  const rawMappings = isPlainObject(rawConfig.mappings)
    ? rawConfig.mappings
    : rawConfig;

  const mappings = Object.entries(rawMappings).map(([name, rawEntry]) => [
    name,
    normalizeReverseEntry(name, rawEntry),
  ]);

  return {
    mappings: Object.fromEntries(mappings),
    bySource: Object.fromEntries(mappings),
  };
}

export function coerceCanonicalValues(values = {}, catalog) {
  if (!isPlainObject(values)) {
    throw new Error('values must be an object of numeric coefficients');
  }

  const normalized = {};
  for (const [name, rawValue] of Object.entries(values)) {
    const numeric = Number(rawValue);
    if (!Number.isFinite(numeric)) {
      throw new Error(`values.${name} must be a finite number`);
    }
    normalized[name] = clampCanonicalValue(name, numeric, catalog);
  }
  return normalized;
}

export function mapCanonicalValuesToAvatar(values = {}, catalog) {
  const normalized = coerceCanonicalValues(values, catalog);
  const mapped = {};

  for (const [name, value] of Object.entries(normalized)) {
    const entry = catalog?.byName?.[name];
    if (!entry) continue;

    applyScaledTargets(mapped, entry.targets, value);

    const hasSplitTargets = Object.keys(entry.positiveTargets).length || Object.keys(entry.negativeTargets).length;
    if (!hasSplitTargets) continue;

    if (value > 0) {
      applyScaledTargets(mapped, entry.positiveTargets, value);
      applyScaledTargets(mapped, entry.negativeTargets, 0);
      continue;
    }

    if (value < 0) {
      applyScaledTargets(mapped, entry.positiveTargets, 0);
      applyScaledTargets(mapped, entry.negativeTargets, Math.abs(value));
      continue;
    }

    applyScaledTargets(mapped, entry.positiveTargets, 0);
    applyScaledTargets(mapped, entry.negativeTargets, 0);
  }

  return mapped;
}

export function mergeCanonicalValueMaps(catalog, ...maps) {
  const merged = {};

  for (const map of maps) {
    if (!isPlainObject(map)) continue;
    for (const [name, rawValue] of Object.entries(map)) {
      const numeric = Number(rawValue);
      if (!Number.isFinite(numeric)) continue;
      merged[name] = clampCanonicalValue(name, numeric, catalog);
    }
  }

  return merged;
}

export function filterRobotCoefficients(values = {}, catalog, options = {}) {
  const normalized = coerceCanonicalValues(values, catalog);
  const includeDefaults = options?.includeDefaults === true;
  const filtered = {};

  if (includeDefaults && catalog?.robotNames) {
    for (const name of catalog.robotNames) {
      filtered[name] = normalized[name] ?? 0;
    }
    return filtered;
  }

  for (const [name, value] of Object.entries(normalized)) {
    if (!catalog?.byName?.[name]?.forwardToRobot) continue;
    filtered[name] = value;
  }

  return filtered;
}

export function convertAvatarValuesToCanonical(values = {}, reverseMapping, catalog) {
  const normalized = coerceCanonicalValues(values);
  const converted = {};

  for (const [sourceName, value] of Object.entries(normalized)) {
    const targets = reverseMapping?.bySource?.[sourceName];
    if (!targets) continue;

    for (const [targetName, scale] of Object.entries(targets)) {
      const nextValue = (converted[targetName] || 0) + value * scale;
      converted[targetName] = clampCanonicalValue(targetName, nextValue, catalog);
    }
  }

  return converted;
}

export function normalizeIncomingValues(values = {}, reverseMapping, catalog) {
  const rawValues = coerceCanonicalValues(values);
  const directValues = {};
  const legacyValues = {};

  for (const [name, value] of Object.entries(rawValues)) {
    const canonicalName = resolveCanonicalAlias(name, catalog);
    if (canonicalName) {
      directValues[canonicalName] = clampCanonicalValue(canonicalName, value, catalog);
      continue;
    }
    legacyValues[name] = value;
  }

  const convertedLegacy = convertAvatarValuesToCanonical(legacyValues, reverseMapping, catalog);
  return mergeCanonicalValueMaps(catalog, convertedLegacy, directValues);
}
