const DEFAULT_ACTION_DURATION_MS = 1800;
const DEFAULT_EXPRESSION_DURATION_MS = 1500;
const DEFAULT_FADE_MS = 300;

export const CONTROL_PROTOCOL_VERSION = '1.0';
export const CONTROL_COMMAND_TYPES = [
  'play_motion',
  'play_sequence',
  'set_mood',
  'set_expression',
  'set_frame',
  'stop',
  'list_motions',
  'ping',
];

function ensureFiniteNumber(value, fieldName) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${fieldName} must be a finite number`);
  }
  return value;
}

function normalizeDurationMs(value, fieldName = 'duration_ms') {
  if (value === undefined || value === null || value === '') return undefined;
  const normalized = Math.round(ensureFiniteNumber(Number(value), fieldName));
  if (normalized <= 0) {
    throw new Error(`${fieldName} must be greater than 0`);
  }
  return normalized;
}

function normalizeNonNegativeMs(value, fieldName = 'ms') {
  if (value === undefined || value === null || value === '') return undefined;
  const normalized = Math.round(ensureFiniteNumber(Number(value), fieldName));
  if (normalized < 0) {
    throw new Error(`${fieldName} must be greater than or equal to 0`);
  }
  return normalized;
}

function buildDefaultTimeline(totalMs) {
  const normalizedTotal = normalizeDurationMs(totalMs, 'duration_ms') || DEFAULT_ACTION_DURATION_MS;
  if (normalizedTotal <= DEFAULT_FADE_MS * 2) {
    return [Math.max(1, Math.round(normalizedTotal / 2)), Math.max(1, normalizedTotal - Math.round(normalizedTotal / 2))];
  }
  return [
    DEFAULT_FADE_MS,
    Math.max(1, normalizedTotal - DEFAULT_FADE_MS * 2),
    DEFAULT_FADE_MS,
  ];
}

function normalizeBlendshapes(blendshapes) {
  if (blendshapes === undefined) return undefined;
  if (!blendshapes || typeof blendshapes !== 'object' || Array.isArray(blendshapes)) {
    throw new Error('blendshapes must be an object of morph coefficients');
  }

  const normalized = {};
  for (const [name, value] of Object.entries(blendshapes)) {
    if (Array.isArray(value)) {
      if (!value.length) throw new Error(`blendshapes.${name} must not be an empty array`);
      normalized[name] = value.map((item) => ensureFiniteNumber(Number(item), `blendshapes.${name}`));
      continue;
    }
    normalized[name] = ensureFiniteNumber(Number(value), `blendshapes.${name}`);
  }
  return normalized;
}

function normalizeValueMap(values, fieldName = 'values') {
  if (values === undefined) return undefined;
  if (!values || typeof values !== 'object' || Array.isArray(values)) {
    throw new Error(`${fieldName} must be an object of numeric values`);
  }

  const normalized = {};
  for (const [name, value] of Object.entries(values)) {
    normalized[name] = ensureFiniteNumber(Number(value), `${fieldName}.${name}`);
  }
  return normalized;
}

function normalizeBones(bones) {
  if (bones === undefined) return undefined;
  if (!bones || typeof bones !== 'object' || Array.isArray(bones)) {
    throw new Error('bones must be an object');
  }

  const normalized = {};
  for (const [boneName, boneState] of Object.entries(bones)) {
    if (!boneState || typeof boneState !== 'object' || Array.isArray(boneState)) {
      throw new Error(`bones.${boneName} must be an object`);
    }
    const entry = {};
    if (boneState.quaternion !== undefined) {
      if (!Array.isArray(boneState.quaternion) || boneState.quaternion.length !== 3) {
        throw new Error(`bones.${boneName}.quaternion must be a 3-item array`);
      }
      entry.quaternion = boneState.quaternion.map((value, index) =>
        ensureFiniteNumber(Number(value), `bones.${boneName}.quaternion[${index}]`));
    }
    if (boneState.position !== undefined) {
      if (!Array.isArray(boneState.position) || boneState.position.length !== 3) {
        throw new Error(`bones.${boneName}.position must be a 3-item array`);
      }
      entry.position = boneState.position.map((value, index) =>
        ensureFiniteNumber(Number(value), `bones.${boneName}.position[${index}]`));
    }
    normalized[boneName] = entry;
  }
  return normalized;
}

function normalizeGesture(gesture) {
  if (gesture === undefined || gesture === null) return undefined;
  if (!Array.isArray(gesture) || !gesture.length) {
    throw new Error('gesture must be an array like [name, duration, isRight]');
  }
  const [name, duration, isRight] = gesture;
  if (typeof name !== 'string' || !name.trim()) {
    throw new Error('gesture[0] must be a non-empty string');
  }
  return [
    name.trim(),
    duration === null || duration === undefined ? null : normalizeDurationMs(duration, 'gesture[1]'),
    isRight === undefined ? undefined : Boolean(isRight),
  ];
}

function normalizeRuntimeMotion(runtimeMotion) {
  if (runtimeMotion === undefined) return undefined;
  if (!runtimeMotion || typeof runtimeMotion !== 'object' || Array.isArray(runtimeMotion)) {
    throw new Error('runtime_motion must be an object');
  }

  const normalized = structuredClone(runtimeMotion);
  if (normalized.dt !== undefined) {
    const dtArray = Array.isArray(normalized.dt) ? normalized.dt : [normalized.dt];
    normalized.dt = dtArray.map((value, index) => {
      if (Array.isArray(value)) {
        return value.map((item) => normalizeDurationMs(item, `runtime_motion.dt[${index}]`));
      }
      return normalizeDurationMs(value, `runtime_motion.dt[${index}]`);
    });
  }

  if (normalized.vs !== undefined) {
    if (!normalized.vs || typeof normalized.vs !== 'object' || Array.isArray(normalized.vs)) {
      throw new Error('runtime_motion.vs must be an object');
    }
    const nextVs = {};
    for (const [name, value] of Object.entries(normalized.vs)) {
      if (name === 'gesture') {
        nextVs[name] = value;
        continue;
      }
      if (Array.isArray(value)) {
        nextVs[name] = value.map((item, index) => ensureFiniteNumber(Number(item), `runtime_motion.vs.${name}[${index}]`));
        continue;
      }
      nextVs[name] = ensureFiniteNumber(Number(value), `runtime_motion.vs.${name}`);
    }
    normalized.vs = nextVs;
  }

  return normalized;
}

function normalizeSequence(sequence) {
  if (!Array.isArray(sequence) || !sequence.length) {
    throw new Error('payload.sequence must be a non-empty array');
  }

  return sequence.map((item, index) => {
    if (typeof item !== 'string' || !item.trim()) {
      throw new Error(`payload.sequence[${index}] must be a non-empty string`);
    }
    return item.trim();
  });
}

function inferTrack(name, motion) {
  if (motion?._track) return motion._track;
  if (['neutral', 'happy', 'relax'].includes(name)) return 'mood';
  return 'action';
}

function generateRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `req_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
}

function withDefaultSentAt(value) {
  if (!value) return new Date().toISOString();
  return value;
}

function wrapVsValues(values = {}) {
  const next = {};
  for (const [name, value] of Object.entries(values)) {
    next[name] = Array.isArray(value) ? value : [value];
  }
  return next;
}

export function estimateMotionDurationMs(motion) {
  if (!motion?.dt) return 0;
  const dtArray = Array.isArray(motion.dt) ? motion.dt : [motion.dt];
  return dtArray.reduce((total, frame) => {
    if (Array.isArray(frame)) {
      const [min, max] = frame;
      return total + ((Number(min) + Number(max)) / 2);
    }
    return total + Number(frame);
  }, 0);
}

export function scaleMotionTimings(dt, targetDurationMs) {
  const normalizedTarget = normalizeDurationMs(targetDurationMs, 'duration_ms');
  if (!dt) return buildDefaultTimeline(normalizedTarget);

  const sourceFrames = Array.isArray(dt) ? dt : [dt];
  const sourceTotal = estimateMotionDurationMs({ dt: sourceFrames }) || normalizedTarget;
  const ratio = normalizedTarget / sourceTotal;

  return sourceFrames.map((frame) => {
    if (Array.isArray(frame)) {
      return frame.map((value) => Math.max(1, Math.round(Number(value) * ratio)));
    }
    return Math.max(1, Math.round(Number(frame) * ratio));
  });
}

export function buildMotionCatalog(...sources) {
  const catalog = {};

  for (const source of sources) {
    if (!source?.motions) continue;

    for (const [name, motion] of Object.entries(source.motions)) {
      catalog[name] = {
        name,
        source: source.source || 'unknown',
        track: inferTrack(name, motion),
        description: motion._description || name,
        tags: motion._tags || [],
        durationMs: estimateMotionDurationMs(motion),
        hasOverlay: Boolean(motion._overlay),
        hasVs: Boolean(motion.vs),
        motion,
      };
    }
  }

  return catalog;
}

export function summarizeMotionCatalog(catalog) {
  const summary = {
    total: 0,
    byTrack: {},
    bySource: {},
  };

  for (const entry of Object.values(catalog)) {
    summary.total += 1;
    summary.byTrack[entry.track] = (summary.byTrack[entry.track] || 0) + 1;
    summary.bySource[entry.source] = (summary.bySource[entry.source] || 0) + 1;
  }

  return summary;
}

export function createExpressionMotion(blendshapes, durationMs = DEFAULT_EXPRESSION_DURATION_MS) {
  const normalizedBlendshapes = normalizeBlendshapes(blendshapes);
  return {
    _track: 'action',
    dt: buildDefaultTimeline(durationMs),
    vs: wrapVsValues(normalizedBlendshapes),
  };
}

export function createRuntimeMotion({
  baseMotion,
  durationMs,
  blendshapes,
  track,
} = {}) {
  const entry = structuredClone(baseMotion || {});
  const normalizedBlendshapes = normalizeBlendshapes(blendshapes);

  if (normalizedBlendshapes) {
    entry.vs = {
      ...(entry.vs || {}),
      ...wrapVsValues(normalizedBlendshapes),
    };
  }

  entry._track = track || entry._track || 'action';
  entry.dt = durationMs
    ? scaleMotionTimings(entry.dt, durationMs)
    : (entry.dt || buildDefaultTimeline(DEFAULT_ACTION_DURATION_MS));

  if (!entry.vs || !Object.keys(entry.vs).length) {
    throw new Error('Runtime motion requires a base motion or explicit blendshapes');
  }

  return entry;
}

function normalizePayload(type, payload = {}, catalog = {}) {
  switch (type) {
    case 'play_motion': {
      const motionName = typeof payload.motion === 'string' ? payload.motion.trim() : '';
      if (!motionName) throw new Error('payload.motion is required for play_motion');
      const runtimeMotion = normalizeRuntimeMotion(payload.runtime_motion);
      const known = catalog[motionName];
      if (Object.keys(catalog).length && !known && !runtimeMotion) {
        throw new Error(`Unknown motion "${motionName}"`);
      }

      return {
        motion: motionName,
        library: payload.library || known?.source || undefined,
        track: payload.track || known?.track || undefined,
        duration_ms: normalizeDurationMs(payload.duration_ms),
        blendshapes: normalizeBlendshapes(payload.blendshapes),
        runtime_motion: runtimeMotion,
        interrupt: payload.interrupt !== false,
      };
    }

    case 'play_sequence':
      return {
        sequence: normalizeSequence(payload.sequence),
        gap_ms: normalizeDurationMs(payload.gap_ms, 'gap_ms'),
      };

    case 'set_mood': {
      const mood = typeof payload.mood === 'string' ? payload.mood.trim() : '';
      if (!mood) throw new Error('payload.mood is required for set_mood');
      return {
        mood,
        duration_ms: normalizeDurationMs(payload.duration_ms),
      };
    }

    case 'set_expression':
      return {
        values: normalizeValueMap(payload.values ?? payload.blendshapes),
        duration_ms: normalizeDurationMs(payload.duration_ms) || DEFAULT_EXPRESSION_DURATION_MS,
      };

    case 'set_frame':
      return {
        stream_id: typeof payload.stream_id === 'string' ? payload.stream_id.trim() : '',
        motion: typeof payload.motion === 'string' ? payload.motion.trim() : '',
        t_ms: normalizeNonNegativeMs(payload.t_ms, 't_ms') || 0,
        duration_ms: normalizeDurationMs(payload.duration_ms) || DEFAULT_FADE_MS,
        values: normalizeValueMap(payload.values ?? payload.blendshapes),
        bones: normalizeBones(payload.bones),
        gesture: normalizeGesture(payload.gesture),
        reset_missing: payload.reset_missing !== false,
        is_first: Boolean(payload.is_first),
        is_last: Boolean(payload.is_last),
      };

    case 'stop':
      return {
        reset_mood: Boolean(payload.reset_mood),
      };

    case 'list_motions':
      return {
        filter: typeof payload.filter === 'string' ? payload.filter.trim() : '',
      };

    case 'ping':
      return {
        echo: payload.echo || 'pong',
      };

    default:
      throw new Error(`Unsupported command type "${type}"`);
  }
}

export function validateControlEnvelope(raw, catalog = {}) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('Control message must be a JSON object');
  }

  const type = typeof raw.type === 'string' ? raw.type.trim() : '';
  if (!CONTROL_COMMAND_TYPES.includes(type)) {
    throw new Error(`type must be one of: ${CONTROL_COMMAND_TYPES.join(', ')}`);
  }

  return {
    version: raw.version || CONTROL_PROTOCOL_VERSION,
    request_id: raw.request_id || generateRequestId(),
    sent_at: withDefaultSentAt(raw.sent_at),
    avatar_id: raw.avatar_id || 'default',
    type,
    payload: normalizePayload(type, raw.payload || {}, catalog),
  };
}

export function createAck(envelope, status, extra = {}) {
  if (!envelope?.request_id) {
    throw new Error('createAck requires a normalized envelope');
  }

  return {
    version: CONTROL_PROTOCOL_VERSION,
    request_id: envelope.request_id,
    avatar_id: envelope.avatar_id || 'default',
    type: envelope.type,
    status,
    updated_at: new Date().toISOString(),
    ...extra,
  };
}
