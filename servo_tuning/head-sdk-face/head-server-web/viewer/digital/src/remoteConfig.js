export const DEFAULT_REMOTE_CONFIG = Object.freeze({
  hub: {
    publicUrl: 'http://127.0.0.1:8765',
    host: '127.0.0.1',
    port: 8765,
    autoConnectOnLoad: true,
  },
  robot: {
    host: '127.0.0.1',
    port: 2543,
    avatarControlOnLoad: false,
  },
  stream: {
    websocketPath: '/ws',
    controlRobotOnLoad: false,
  },
  viewer: {
    expressionResetMs: 1200,
    coefficientConfigUrl: './blendshape-config.yaml',
  },
});

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function mergeObjects(base, overrides) {
  const result = structuredClone(base);

  if (!isPlainObject(overrides)) {
    return result;
  }

  for (const [key, value] of Object.entries(overrides)) {
    if (isPlainObject(value) && isPlainObject(result[key])) {
      result[key] = mergeObjects(result[key], value);
      continue;
    }
    result[key] = value;
  }

  return result;
}

function normalizeString(value, fallback) {
  const normalized = String(value ?? '').trim();
  return normalized || fallback;
}

function normalizePort(value, fallback) {
  const port = Number(value);
  return Number.isInteger(port) && port > 0 ? port : fallback;
}

function normalizeBoolean(value, fallback) {
  return typeof value === 'boolean' ? value : fallback;
}

function normalizeExternalPath(value, fallback) {
  const raw = normalizeString(value, fallback);
  return raw.startsWith('/') ? raw : `/${raw}`;
}

function normalizeRelativeUrl(value, fallback) {
  const raw = normalizeString(value, fallback);
  if (raw.startsWith('./') || raw.startsWith('../') || raw.startsWith('/')) {
    return raw;
  }
  return `./${raw}`;
}

function buildDefaultHubUrl(host, port) {
  return `http://${host}:${port}`;
}

export function mergeRemoteConfig(rawConfig = {}) {
  return normalizeRemoteConfig(mergeObjects(DEFAULT_REMOTE_CONFIG, rawConfig));
}

export function normalizeRemoteConfig(rawConfig = {}) {
  const merged = mergeObjects(DEFAULT_REMOTE_CONFIG, rawConfig);

  const hubHost = normalizeString(merged.hub?.host, DEFAULT_REMOTE_CONFIG.hub.host);
  const hubPort = normalizePort(merged.hub?.port, DEFAULT_REMOTE_CONFIG.hub.port);
  const hubPublicUrl = normalizeString(
    merged.hub?.publicUrl,
    buildDefaultHubUrl(hubHost, hubPort),
  );

  const stream = {
    websocketPath: normalizeExternalPath(
      merged.stream?.websocketPath,
      DEFAULT_REMOTE_CONFIG.stream.websocketPath,
    ),
    controlRobotOnLoad: normalizeBoolean(
      merged.stream?.controlRobotOnLoad,
      DEFAULT_REMOTE_CONFIG.stream.controlRobotOnLoad,
    ),
  };

  return {
    hub: {
      publicUrl: hubPublicUrl,
      host: hubHost,
      port: hubPort,
      autoConnectOnLoad: normalizeBoolean(
        merged.hub?.autoConnectOnLoad,
        DEFAULT_REMOTE_CONFIG.hub.autoConnectOnLoad,
      ),
    },
    robot: {
      host: normalizeString(merged.robot?.host, DEFAULT_REMOTE_CONFIG.robot.host),
      port: normalizePort(merged.robot?.port, DEFAULT_REMOTE_CONFIG.robot.port),
      avatarControlOnLoad: normalizeBoolean(
        merged.robot?.avatarControlOnLoad,
        DEFAULT_REMOTE_CONFIG.robot.avatarControlOnLoad,
      ),
    },
    stream,
    viewer: {
      expressionResetMs: normalizePort(
        merged.viewer?.expressionResetMs,
        DEFAULT_REMOTE_CONFIG.viewer.expressionResetMs,
      ),
      coefficientConfigUrl: normalizeRelativeUrl(
        merged.viewer?.coefficientConfigUrl,
        DEFAULT_REMOTE_CONFIG.viewer.coefficientConfigUrl,
      ),
    },
  };
}

export function buildStreamWebSocketUrl(hubUrl, websocketPath = DEFAULT_REMOTE_CONFIG.stream.websocketPath) {
  const fallbackHubUrl = DEFAULT_REMOTE_CONFIG.hub.publicUrl;

  try {
    const resolved = new URL(hubUrl || fallbackHubUrl);
    resolved.protocol = resolved.protocol === 'https:' ? 'wss:' : 'ws:';
    resolved.pathname = normalizeExternalPath(websocketPath, DEFAULT_REMOTE_CONFIG.stream.websocketPath);
    resolved.search = '';
    resolved.hash = '';
    return resolved.toString();
  } catch {
    return '';
  }
}
