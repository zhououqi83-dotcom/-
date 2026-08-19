import {
  normalizeIncomingValues,
} from './blendshapeMapping.js';

const RESERVED_EXTERNAL_FRAME_KEYS = new Set([
  'avatar_id',
  'blendshapes',
  'bones',
  'detail',
  'duration_ms',
  'gesture',
  'interrupt',
  'is_first',
  'is_last',
  'library',
  'motion',
  'ok',
  'payload',
  'received_at',
  'request_id',
  'reset_missing',
  'sent_at',
  'status',
  'stream_id',
  't_ms',
  'track',
  'type',
  'values',
  'version',
]);

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function unwrapExternalPayload(rawPayload = {}) {
  if (!isPlainObject(rawPayload)) {
    throw new Error('external frame must be a JSON object');
  }

  if (isPlainObject(rawPayload.payload)) {
    return unwrapExternalPayload(rawPayload.payload);
  }

  return rawPayload;
}

function extractTopLevelValues(frame) {
  const values = {};

  for (const [name, value] of Object.entries(frame)) {
    if (RESERVED_EXTERNAL_FRAME_KEYS.has(name)) continue;
    values[name] = value;
  }

  return values;
}

export function normalizeExternalBones(bones) {
  if (bones === undefined) return {};
  if (!isPlainObject(bones)) {
    throw new Error('bones must be an object');
  }
  return structuredClone(bones);
}

export function normalizeExternalGesture(gesture) {
  if (gesture === undefined) return undefined;
  if (gesture === null) return null;
  if (!Array.isArray(gesture) || !gesture.length) {
    throw new Error('gesture must be an array like [name, duration, isRight]');
  }

  const [name, duration, isRight] = gesture;
  if (typeof name !== 'string' || !name.trim()) {
    throw new Error('gesture[0] must be a non-empty string');
  }

  const normalized = [name.trim()];
  if (duration === null || duration === undefined || duration === '') {
    normalized.push(null);
  } else {
    const numericDuration = Math.round(Number(duration));
    if (!Number.isFinite(numericDuration) || numericDuration <= 0) {
      throw new Error('gesture[1] must be a positive number or null');
    }
    normalized.push(numericDuration);
  }

  if (isRight !== undefined) {
    normalized.push(Boolean(isRight));
  }

  return normalized;
}

function normalizeOptionalString(value, fieldName) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${fieldName} must be a non-empty string`);
  }
  return value.trim();
}

function normalizeOptionalInteger(value, fieldName, { min = 0 } = {}) {
  if (value === undefined || value === null || value === '') return undefined;
  const numeric = Math.round(Number(value));
  if (!Number.isFinite(numeric) || numeric < min) {
    throw new Error(`${fieldName} must be an integer >= ${min}`);
  }
  return numeric;
}

function normalizeOptionalBoolean(value, fieldName) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'boolean') {
    throw new Error(`${fieldName} must be a boolean`);
  }
  return value;
}

export function buildExternalFrame(rawPayload = {}, { motionMapping, coefficientCatalog } = {}) {
  const frame = unwrapExternalPayload(rawPayload);
  const rawValues = frame.values
    ?? frame.blendshapes
    ?? extractTopLevelValues(frame);

  const normalizedFrame = {
    values: Object.keys(rawValues || {}).length
      ? normalizeIncomingValues(rawValues, motionMapping, coefficientCatalog)
      : {},
    bones: normalizeExternalBones(frame.bones),
    reset_missing: frame.reset_missing !== false,
  };

  if (hasOwn(frame, 'gesture')) {
    normalizedFrame.gesture = normalizeExternalGesture(frame.gesture);
  }

  const streamId = normalizeOptionalString(frame.stream_id, 'stream_id');
  if (streamId !== undefined) {
    normalizedFrame.stream_id = streamId;
  }

  const motionName = normalizeOptionalString(frame.motion, 'motion');
  if (motionName !== undefined) {
    normalizedFrame.motion = motionName;
  }

  const tMs = normalizeOptionalInteger(frame.t_ms, 't_ms');
  if (tMs !== undefined) {
    normalizedFrame.t_ms = tMs;
  }

  const durationMs = normalizeOptionalInteger(frame.duration_ms, 'duration_ms', { min: 1 });
  if (durationMs !== undefined) {
    normalizedFrame.duration_ms = durationMs;
  }

  const isFirst = normalizeOptionalBoolean(frame.is_first, 'is_first');
  if (isFirst !== undefined) {
    normalizedFrame.is_first = isFirst;
  }

  const isLast = normalizeOptionalBoolean(frame.is_last, 'is_last');
  if (isLast !== undefined) {
    normalizedFrame.is_last = isLast;
  }

  return normalizedFrame;
}
