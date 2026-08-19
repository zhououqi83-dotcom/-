import { TalkingHead } from 'talkinghead';
import {
  buildStreamWebSocketUrl,
  mergeRemoteConfig,
} from '../src/remoteConfig.js';
import {
  filterRobotCoefficients,
  mapCanonicalValuesToAvatar,
  mergeCanonicalValueMaps,
  normalizeCoefficientCatalog,
} from '../src/blendshapeMapping.js';
import { parseSimpleYaml } from '../src/simpleYaml.js';

const AVATAR_MODEL = './female_1.glb';
const AVATAR_BODY = 'F';
const REMOTE_CONFIG_URL = './config.yaml';

const container = document.getElementById('avatar-container');
const statusEl = document.getElementById('status');
const hubInput = document.getElementById('hub-url');
const logEl = document.getElementById('log');
const lastCommandEl = document.getElementById('last-command');
const btnConnect = document.getElementById('btn-connect');
const btnDisconnect = document.getElementById('btn-disconnect');
const blendshapeControlsEl = document.getElementById('blendshape-controls');
const jointControlsEl = document.getElementById('joint-controls');
const robotHostInput = document.getElementById('robot-host');
const robotPortInput = document.getElementById('robot-port');
const robotStatusEl = document.getElementById('robot-status');
const btnRobotConnect = document.getElementById('btn-robot-connect');
const btnRobotDisconnect = document.getElementById('btn-robot-disconnect');
const btnAvatarRobot = document.getElementById('btn-avatar-robot');
const mappingPathInput = document.getElementById('mapping-path');
const btnReloadMapping = document.getElementById('btn-reload-mapping');
const btnExternalRobot = document.getElementById('btn-external-robot');
const externalRouteEl = document.getElementById('external-route');
const externalStatusEl = document.getElementById('external-status');
const btnForceDrag = document.getElementById('btn-force-drag');
const btnResetControls = document.getElementById('btn-reset-controls');
const armControlsEl = document.getElementById('arm-controls');

let runtimeConfig = mergeRemoteConfig();
let head = null;
let eventSource = null;
let hubConnectionState = 'disconnected';
let hubConnectionUrl = '';
let hubRetryNoticeShown = false;
let avatarRobotForwarding = false;
let externalRobotForwarding = false;
let expressionResetTimer = null;
let avatarRobotForwardTimer = null;
let coefficientCatalog = normalizeCoefficientCatalog({ coefficients: {} });
let sliderState = new Map();
let armSliderState = new Map();
let blendshapeKeys = [];
let jointKeys = [];
let latestExternalValues = {};
let manualCoefficientValues = {};
let manualAppliedAvatarKeys = new Set();
let manualBoneControlState = {};
let forceManualDrag = false;
let suppressAvatarRobotForwardDepth = 0;
const viewerId = `viewer_${Math.random().toString(16).slice(2, 10)}`;
const directFrameState = {
  streamId: null,
  values: {},
  bones: {},
  appliedValueKeys: new Set(),
  appliedBoneKeys: new Set(),
  gestureKey: null,
};

const ARM_BONE_CONTROL_DEFS = Object.freeze([
  { key: 'LeftForeArm.x', label: '左前臂 X', bone: 'LeftForeArm', axisIndex: 0 },
  { key: 'LeftForeArm.y', label: '左前臂 Y', bone: 'LeftForeArm', axisIndex: 1 },
  { key: 'LeftForeArm.z', label: '左前臂 Z', bone: 'LeftForeArm', axisIndex: 2 },
  { key: 'RightForeArm.x', label: '右前臂 X', bone: 'RightForeArm', axisIndex: 0 },
  { key: 'RightForeArm.y', label: '右前臂 Y', bone: 'RightForeArm', axisIndex: 1 },
  { key: 'RightForeArm.z', label: '右前臂 Z', bone: 'RightForeArm', axisIndex: 2 },
  { key: 'LeftHand.x', label: '左手腕 X', bone: 'LeftHand', axisIndex: 0 },
  { key: 'LeftHand.y', label: '左手腕 Y', bone: 'LeftHand', axisIndex: 1 },
  { key: 'LeftHand.z', label: '左手腕 Z', bone: 'LeftHand', axisIndex: 2 },
  { key: 'RightHand.x', label: '右手腕 X', bone: 'RightHand', axisIndex: 0 },
  { key: 'RightHand.y', label: '右手腕 Y', bone: 'RightHand', axisIndex: 1 },
  { key: 'RightHand.z', label: '右手腕 Z', bone: 'RightHand', axisIndex: 2 },
]);
const ARM_BONE_CONTROL_MAP = Object.fromEntries(
  ARM_BONE_CONTROL_DEFS.map((entry) => [entry.key, entry]),
);

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function log(message, level = 'info') {
  const line = `[${new Date().toLocaleTimeString()}] ${level.toUpperCase()} ${message}`;
  logEl.textContent += `${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function applyHubConnectionState() {
  btnConnect.disabled = hubConnectionState === 'connecting' || hubConnectionState === 'connected';
  btnDisconnect.disabled = hubConnectionState === 'disconnected';
}

function setHubConnectionState(nextState) {
  hubConnectionState = nextState;
  applyHubConnectionState();
}

function applyRuntimeConfig(config = mergeRemoteConfig()) {
  runtimeConfig = config;
  hubInput.value = runtimeConfig.hub.publicUrl;
  robotHostInput.value = runtimeConfig.robot.host;
  robotPortInput.value = String(runtimeConfig.robot.port);
  avatarRobotForwarding = runtimeConfig.robot.avatarControlOnLoad;
  externalRobotForwarding = runtimeConfig.stream.controlRobotOnLoad;
  applyAvatarRobotForwardingState();
  applyExternalRobotForwardingState();
  updateExternalRoute();
}

async function loadRuntimeConfig() {
  try {
    const response = await fetch(REMOTE_CONFIG_URL, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`Failed to load remote config: ${response.status}`);
    }

    const yaml = await response.text();
    applyRuntimeConfig(mergeRemoteConfig(parseSimpleYaml(yaml)));
  } catch (error) {
    applyRuntimeConfig(mergeRemoteConfig());
    log(`Config fallback: ${error.message}`, 'warn');
  }
}

async function loadCoefficientCatalog() {
  try {
    const response = await fetch(runtimeConfig.viewer.coefficientConfigUrl, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`Failed to load coefficient config: ${response.status}`);
    }

    const yaml = await response.text();
    coefficientCatalog = normalizeCoefficientCatalog(parseSimpleYaml(yaml));
  } catch (error) {
    coefficientCatalog = normalizeCoefficientCatalog({ coefficients: {} });
    log(`Coefficient config fallback: ${error.message}`, 'warn');
  }
}

function getHubUrl() {
  return (hubInput.value || runtimeConfig.hub.publicUrl).trim().replace(/\/$/, '');
}

function getExternalWebSocketUrl() {
  return buildStreamWebSocketUrl(getHubUrl(), runtimeConfig.stream.websocketPath);
}

function updateExternalRoute() {
  const url = getExternalWebSocketUrl();
  externalRouteEl.textContent = url
    ? `流式发送入口：${url}`
    : '流式发送入口不可用';
}

async function fetchJson(path, options = {}) {
  const response = await fetch(`${getHubUrl()}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.error || `Request failed: ${path}`);
  }
  return data;
}

function freezeAvatarIdle() {
  if (!head?.animMoods) return;
  head.animMoods.frozen = {
    baseline: {},
    speech: { deltaRate: 0, deltaPitch: 0, deltaVolume: 0 },
    anims: [],
  };
  head.setMood('frozen');
  head.opt.avatarIdleEyeContact = 0;
  head.opt.avatarIdleHeadMove = 0;
  head.opt.avatarSpeakingEyeContact = 0;
  head.opt.avatarSpeakingHeadMove = 0;
  head.animQueue = [];
}

function resetAvatarDefaultPose() {
  if (!head?.poseTemplates?.straight || typeof head.setPoseFromTemplate !== 'function') return;
  head.poseName = 'straight';
  head.setPoseFromTemplate(head.poseTemplates.straight, 0);
}

function updateSliderValue(key, value) {
  const entry = sliderState.get(key);
  if (!entry) return;
  const numeric = Number.isFinite(Number(value)) ? Number(value) : 0;
  entry.input.value = String(numeric);
  entry.output.textContent = numeric.toFixed(2);
}

function updateArmSliderValue(key, value) {
  const entry = armSliderState.get(key);
  if (!entry) return;
  const numeric = Number.isFinite(Number(value)) ? Number(value) : 0;
  entry.input.value = String(numeric);
  entry.output.textContent = numeric.toFixed(2);
}

function setAvatarTargetValue(key, value, channel) {
  const target = head?.mtAvatar?.[key];
  if (!target) return false;
  target[channel] = value;
  target.needsUpdate = true;
  return true;
}

function clearAvatarTargetValue(key, channel) {
  const target = head?.mtAvatar?.[key];
  if (!target) return false;
  target[channel] = null;
  target.needsUpdate = true;
  return true;
}

function applyAvatarTargetMap(values = {}, channel, previousKeys = new Set()) {
  const nextKeys = new Set(Object.keys(values));

  for (const key of previousKeys) {
    if (!nextKeys.has(key)) clearAvatarTargetValue(key, channel);
  }

  for (const [key, value] of Object.entries(values)) {
    setAvatarTargetValue(key, value, channel);
  }

  return nextKeys;
}

function applyManualValues() {
  const mapped = mapCanonicalValuesToAvatar(getEffectiveManualCoefficientValues(), coefficientCatalog);
  manualAppliedAvatarKeys = applyAvatarTargetMap(mapped, 'fixed', manualAppliedAvatarKeys);
  refreshCoefficientSliderState();
}

function applyDirectValueState() {
  const mapped = mapCanonicalValuesToAvatar(directFrameState.values, coefficientCatalog);
  directFrameState.appliedValueKeys = applyAvatarTargetMap(
    mapped,
    'realtime',
    directFrameState.appliedValueKeys,
  );
  refreshCoefficientSliderState();
}

function setManualCoefficientValue(key, value) {
  manualCoefficientValues = {
    ...manualCoefficientValues,
    [key]: Number(value),
  };
  updateSliderValue(key, value);
  applyManualValues();
  scheduleAvatarRobotForward();
}

function clearManualCoefficientValue(key) {
  if (!(key in manualCoefficientValues)) {
    updateSliderValue(key, 0);
    return;
  }
  const nextValues = { ...manualCoefficientValues };
  delete nextValues[key];
  manualCoefficientValues = nextValues;
  updateSliderValue(key, 0);
  applyManualValues();
  scheduleAvatarRobotForward();
}

function getCurrentCanonicalValues() {
  return mergeCanonicalValueMaps(
    coefficientCatalog,
    directFrameState.values,
    getEffectiveManualCoefficientValues(),
  );
}

function getEffectiveManualCoefficientValues() {
  if (forceManualDrag) {
    return { ...manualCoefficientValues };
  }

  const effectiveValues = {};
  for (const [key, value] of Object.entries(manualCoefficientValues)) {
    if (!hasOwn(directFrameState.values, key)) {
      effectiveValues[key] = value;
    }
  }
  return effectiveValues;
}

function scheduleAvatarRobotForward() {
  if (!avatarRobotForwarding || suppressAvatarRobotForwardDepth > 0 || avatarRobotForwardTimer) return;

  avatarRobotForwardTimer = setTimeout(async () => {
    avatarRobotForwardTimer = null;
    try {
      await sendRobotBlendshapes(
        filterRobotCoefficients(getCurrentCanonicalValues(), coefficientCatalog, {
          includeDefaults: true,
        }),
        'avatar',
      );
    } catch (error) {
      log(error.message, 'error');
      updateRobotStatus({ ok: false, error: error.message });
    }
  }, 80);
}

function withAvatarRobotForwardSuppressed(applyChanges) {
  suppressAvatarRobotForwardDepth += 1;
  try {
    applyChanges();
  } finally {
    suppressAvatarRobotForwardDepth -= 1;
  }
}

function applyAvatarRobotForwardingState() {
  btnAvatarRobot.classList.toggle('active', avatarRobotForwarding);
  btnAvatarRobot.textContent = avatarRobotForwarding
    ? '数字人控制机器人：开'
    : '数字人控制机器人：关';
}

function applyExternalRobotForwardingState() {
  btnExternalRobot.classList.toggle('active', externalRobotForwarding);
  btnExternalRobot.textContent = externalRobotForwarding
    ? '流式接口控制机器人：开'
    : '流式接口控制机器人：关';
}

function applyForceDragState() {
  btnForceDrag.classList.toggle('active', forceManualDrag);
  btnForceDrag.textContent = forceManualDrag
    ? '强制拖动：开'
    : '强制拖动：关';
}

function updateExternalStatus(snapshot = null) {
  if (!snapshot) {
    externalStatusEl.textContent = eventSource ? '等待流式数据...' : '等待 Hub...';
    return;
  }

  if (snapshot.websocket_path) {
    runtimeConfig.stream.websocketPath = snapshot.websocket_path;
    updateExternalRoute();
  }

  const connections = Number(snapshot.connections || 0);
  const lastSeen = snapshot.last_message_at
    ? new Date(snapshot.last_message_at).toLocaleTimeString()
    : '暂无数据';
  if (typeof snapshot.robot_forwarding_enabled === 'boolean') {
    externalRobotForwarding = snapshot.robot_forwarding_enabled;
    applyExternalRobotForwardingState();
  }

  const robotForwardSummary = snapshot.last_robot_forward_error
    ? ` / SDK错误 ${snapshot.last_robot_forward_error}`
    : snapshot.last_robot_forward_at
      ? ` / SDK ${new Date(snapshot.last_robot_forward_at).toLocaleTimeString()}`
      : '';

  externalStatusEl.textContent = `发送端 ${connections} 路 / 最近 ${lastSeen}${robotForwardSummary}`;
}

function createSlider(key, { min, max, step, initialValue = 0 }) {
  const wrapper = document.createElement('label');
  wrapper.className = 'slider-row';

  const nameEl = document.createElement('span');
  nameEl.className = 'slider-name';
  nameEl.textContent = key;

  const input = document.createElement('input');
  input.type = 'range';
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(initialValue);

  const output = document.createElement('code');
  output.className = 'slider-value';
  output.textContent = initialValue.toFixed(2);

  input.addEventListener('input', () => {
    setManualCoefficientValue(key, Number(input.value));
  });

  wrapper.append(nameEl, input, output);
  sliderState.set(key, { input, output });
  return wrapper;
}

function createArmSlider(control) {
  const wrapper = document.createElement('label');
  wrapper.className = 'slider-row';

  const nameEl = document.createElement('span');
  nameEl.className = 'slider-name';
  nameEl.textContent = control.label;

  const input = document.createElement('input');
  input.type = 'range';
  input.min = '-1';
  input.max = '1';
  input.step = '0.01';
  input.value = '0';

  const output = document.createElement('code');
  output.className = 'slider-value';
  output.textContent = '0.00';

  input.addEventListener('input', () => {
    setManualBoneControlValue(control.key, Number(input.value));
  });

  wrapper.append(nameEl, input, output);
  armSliderState.set(control.key, { input, output });
  return wrapper;
}

function hasBoneQuaternionControl(control) {
  return Boolean(head?.poseDelta?.props?.[`${control.bone}.quaternion`]);
}

function normalizeBoneState(state = {}) {
  const normalized = {};

  if (Array.isArray(state.quaternion)) {
    const quaternion = state.quaternion.map((value) => {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
      return Number(value);
    }).slice(0, 3);
    while (quaternion.length < 3) quaternion.push(null);
    if (quaternion.some((value) => value !== null)) {
      normalized.quaternion = quaternion;
    }
  }

  if (Array.isArray(state.position)) {
    const position = state.position.map((value) => {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
      return Number(value);
    }).slice(0, 3);
    while (position.length < 3) position.push(null);
    if (position.some((value) => value !== null)) {
      normalized.position = position;
    }
  }

  return normalized;
}

function mergeBoneStateMaps(...maps) {
  const merged = {};

  for (const map of maps) {
    if (!map || typeof map !== 'object' || Array.isArray(map)) continue;

    for (const [boneName, state] of Object.entries(map)) {
      const normalized = normalizeBoneState(state);
      const entry = merged[boneName] || {};

      if (normalized.quaternion) {
        const quaternion = Array.isArray(entry.quaternion) ? [...entry.quaternion] : [null, null, null];
        normalized.quaternion.forEach((value, index) => {
          if (value !== null) quaternion[index] = value;
        });
        entry.quaternion = quaternion;
      }

      if (normalized.position) {
        const position = Array.isArray(entry.position) ? [...entry.position] : [null, null, null];
        normalized.position.forEach((value, index) => {
          if (value !== null) position[index] = value;
        });
        entry.position = position;
      }

      const hasQuaternion = Array.isArray(entry.quaternion) && entry.quaternion.some((value) => value !== null);
      const hasPosition = Array.isArray(entry.position) && entry.position.some((value) => value !== null);
      if (hasQuaternion || hasPosition) {
        merged[boneName] = entry;
      } else {
        delete merged[boneName];
      }
    }
  }

  return merged;
}

function getCombinedBoneStates() {
  if (forceManualDrag) {
    return mergeBoneStateMaps(directFrameState.bones, manualBoneControlState);
  }
  return mergeBoneStateMaps(manualBoneControlState, directFrameState.bones);
}

function refreshCoefficientSliderState() {
  const currentValues = getCurrentCanonicalValues();
  for (const key of [...blendshapeKeys, ...jointKeys]) {
    updateSliderValue(key, currentValues[key] ?? 0);
  }
}

function refreshArmSliderState(bones = getCombinedBoneStates()) {
  for (const control of ARM_BONE_CONTROL_DEFS) {
    if (!armSliderState.has(control.key)) continue;
    const boneState = bones[control.bone];
    const quaternion = Array.isArray(boneState?.quaternion)
      ? boneState.quaternion
      : [0, 0, 0];
    updateArmSliderValue(control.key, quaternion[control.axisIndex] ?? 0);
  }
}

function buildControlPanels() {
  const entries = coefficientCatalog.coefficients || [];
  blendshapeKeys = entries
    .filter((entry) => !['head', 'eye_pose'].includes(entry.group))
    .map((entry) => entry.name);
  jointKeys = entries
    .filter((entry) => ['head', 'eye_pose'].includes(entry.group))
    .map((entry) => entry.name);

  blendshapeControlsEl.innerHTML = '';
  jointControlsEl.innerHTML = '';
  armControlsEl.innerHTML = '';
  sliderState = new Map();
  armSliderState = new Map();

  blendshapeKeys.forEach((key) => {
    const entry = coefficientCatalog.byName[key];
    blendshapeControlsEl.appendChild(createSlider(key, {
      min: entry?.min ?? 0,
      max: entry?.max ?? 1,
      step: entry?.step ?? 0.01,
    }));
  });
  jointKeys.forEach((key) => {
    const entry = coefficientCatalog.byName[key];
    jointControlsEl.appendChild(createSlider(key, {
      min: entry?.min ?? -1,
      max: entry?.max ?? 1,
      step: entry?.step ?? 0.01,
    }));
  });

  ARM_BONE_CONTROL_DEFS
    .filter((control) => hasBoneQuaternionControl(control))
    .forEach((control) => {
      armControlsEl.appendChild(createArmSlider(control));
    });

  refreshCoefficientSliderState();
  refreshArmSliderState();
}

function updateRobotStatus(robot = {}) {
  if (robot?.connectError) {
    robotStatusEl.textContent = `连接失败：${robot.connectError}`;
    return;
  }
  if (!robot?.ok) {
    robotStatusEl.textContent = `不可用：${robot?.error || 'head_sdk 未就绪'}`;
    return;
  }
  if (!robot.connected) {
    robotStatusEl.textContent = '未连接';
    return;
  }
  robotStatusEl.textContent = `已连接 ${robot.target?.host}:${robot.target?.port}`;
}

async function refreshRobotStatus() {
  try {
    const data = await fetchJson('/robot/status');
    updateRobotStatus(data.robot);
  } catch (error) {
    updateRobotStatus({ ok: false, error: error.message });
  }
}

async function refreshExternalStatus() {
  try {
    const data = await fetchJson('/status');
    updateExternalStatus(data.stream);
  } catch (error) {
    updateExternalStatus({
      connections: 0,
      last_message_at: null,
      websocket_path: runtimeConfig.stream.websocketPath,
    });
    log(`Stream status unavailable: ${error.message}`, 'warn');
  }
}

async function connectRobot() {
  try {
    const data = await fetchJson('/robot/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: robotHostInput.value.trim(),
        port: Number(robotPortInput.value || runtimeConfig.robot.port),
      }),
    });
    updateRobotStatus(data.robot);
    if (data.robot?.connected) {
      log('Head SDK connected', 'info');
      return;
    }
    log('Head SDK not connected', 'warn');
  } catch (error) {
    updateRobotStatus({ ok: true, connected: false, connectError: error.message });
    log(error.message, 'error');
  }
}

async function disconnectRobot() {
  try {
    const data = await fetchJson('/robot/disconnect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    updateRobotStatus(data.robot);
    log('Head SDK disconnected', 'warn');
  } catch (error) {
    log(error.message, 'error');
  }
}

async function reloadRobotMapping() {
  try {
    const rawPath = (mappingPathInput?.value || '').trim();
    const data = await fetchJson('/robot/reload-bs2servo-mapping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rawPath ? { path: rawPath } : {}),
    });
    updateRobotStatus(data.robot);
    const mappingPath = data.robot?.mapping_path || '默认映射';
    const mappingCount = Number(data.robot?.mapping_count || 0);
    log(`Robot mapping reloaded: ${mappingPath} (${mappingCount} entries)`, 'info');
  } catch (error) {
    log(error.message, 'error');
    updateRobotStatus({ ok: false, error: error.message });
  }
}

async function sendRobotBlendshapes(values, sourceLabel = 'avatar', { logSend = false } = {}) {
  const data = await fetchJson('/robot/set-arkit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
  updateRobotStatus(data.robot);
  if (logSend) {
    log(`${sourceLabel} -> robot (${Object.keys(values).length} coeffs)`, 'info');
  }
}

function applyExternalFrame(payload = {}) {
  const apply = () => {
    const streamId = typeof payload.stream_id === 'string' && payload.stream_id
      ? payload.stream_id
      : null;
    const isFirst = payload.is_first === true;
    const isLast = payload.is_last === true;
    const carriesStreamState = isFirst || isLast || Boolean(streamId);

    if (isFirst || (streamId && directFrameState.streamId && directFrameState.streamId !== streamId)) {
      resetDirectFrameState();
    }

    if (streamId) {
      directFrameState.streamId = streamId;
    } else if (isFirst) {
      directFrameState.streamId = '__stream_frame__';
    }

    const resetMissing = payload.reset_missing !== false;
    applyDirectValues(payload.values || payload.blendshapes || {}, resetMissing);
    applyDirectBones(payload.bones || {}, resetMissing);
    if (hasOwn(payload, 'gesture')) {
      applyDirectGesture(payload.gesture);
    } else if (resetMissing) {
      applyDirectGesture(null);
    }

    if (isLast) {
      directFrameState.streamId = null;
    }

    // if (!carriesStreamState || isLast) {
    //   resetExpressionLater(payload.duration_ms || runtimeConfig.viewer.expressionResetMs);
    // }
  };

  if (externalRobotForwarding) {
    withAvatarRobotForwardSuppressed(apply);
    return;
  }

  apply();
}

async function setExternalRobotForwarding(enabled) {
  const data = await fetchJson('/robot-forwarding', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  updateExternalStatus(data.stream);
}

async function initAvatar() {
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

  head = new TalkingHead(container, {
    audioCtx,
    showProgressBar: false,
    dracoEnabled: true,
    pcmSampleRate: 16000,
    cameraView: 'full',
  });

  await head.showAvatar({ url: AVATAR_MODEL, body: AVATAR_BODY, avatarMode: 'full-body' });
  head.start();
  freezeAvatarIdle();
  resetAvatarDefaultPose();
  buildControlPanels();
  applyManualValues();
  applyDirectValueState();
  setStatus(`Avatar ready · coefficients ${coefficientCatalog.coefficients.length}`);
  log(`Avatar ready with ${coefficientCatalog.coefficients.length} coefficient sliders`);
}

function applyDirectValues(values = {}, resetMissing = true) {
  directFrameState.values = resetMissing
    ? { ...values }
    : { ...directFrameState.values, ...values };
  applyDirectValueState();
  applyManualValues();
  scheduleAvatarRobotForward();
}

function setManualBoneControlValue(controlKey, value) {
  const control = ARM_BONE_CONTROL_MAP[controlKey];
  if (!control) return;

  const nextState = { ...manualBoneControlState };
  const boneEntry = normalizeBoneState(nextState[control.bone]);
  const quaternion = Array.isArray(boneEntry.quaternion)
    ? [...boneEntry.quaternion]
    : [null, null, null];
  quaternion[control.axisIndex] = Math.abs(Number(value)) < 0.0001 ? null : Number(value);

  const hasQuaternion = quaternion.some((item) => item !== null);
  if (hasQuaternion) {
    nextState[control.bone] = {
      ...boneEntry,
      quaternion,
    };
  } else if (boneEntry.position) {
    nextState[control.bone] = boneEntry;
    delete nextState[control.bone].quaternion;
  } else {
    delete nextState[control.bone];
  }

  manualBoneControlState = nextState;
  applyCombinedBoneState();
}

function clearManualBoneControlValue(controlKey) {
  setManualBoneControlValue(controlKey, 0);
}

function setBoneState(boneName, state = {}) {
  const props = head?.poseDelta?.props;
  if (!props) return;

  const quatKey = `${boneName}.quaternion`;
  const posKey = `${boneName}.position`;
  if (props[quatKey]) {
    const [x, y, z] = state.quaternion || [0, 0, 0];
    props[quatKey].x = x;
    props[quatKey].y = y;
    props[quatKey].z = z;
  }
  if (props[posKey]) {
    const [x, y, z] = state.position || [0, 0, 0];
    props[posKey].x = x;
    props[posKey].y = y;
    props[posKey].z = z;
  }
}

function applyCombinedBoneState() {
  const combinedBones = getCombinedBoneStates();
  const nextKeys = new Set(Object.keys(combinedBones));

  for (const boneName of directFrameState.appliedBoneKeys) {
    if (!nextKeys.has(boneName)) setBoneState(boneName, {});
  }

  for (const [boneName, state] of Object.entries(combinedBones)) {
    const quaternion = Array.isArray(state.quaternion)
      ? state.quaternion.map((value) => (value === null ? 0 : value))
      : undefined;
    const position = Array.isArray(state.position)
      ? state.position.map((value) => (value === null ? 0 : value))
      : undefined;
    setBoneState(boneName, { quaternion, position });
  }

  directFrameState.appliedBoneKeys = nextKeys;
  refreshArmSliderState(combinedBones);
}

function applyDirectBones(bones = {}, resetMissing = true) {
  directFrameState.bones = resetMissing
    ? mergeBoneStateMaps(bones)
    : mergeBoneStateMaps(directFrameState.bones, bones);
  applyCombinedBoneState();
}

function stopDirectGesture() {
  if (!directFrameState.gestureKey) return;
  head?.stopGesture(100);
  directFrameState.gestureKey = null;
}

function applyDirectGesture(gesture) {
  const nextKey = gesture ? JSON.stringify(gesture) : null;
  if (nextKey === directFrameState.gestureKey) return;

  if (!gesture) {
    stopDirectGesture();
    return;
  }

  stopDirectGesture();
  const [name, duration, isRight] = gesture;
  head.playGesture(name, duration ?? Infinity, Boolean(isRight), 0);
  directFrameState.gestureKey = nextKey;
}

function resetDirectFrameState() {
  if (expressionResetTimer) {
    clearTimeout(expressionResetTimer);
    expressionResetTimer = null;
  }
  applyDirectValues({}, true);
  applyDirectBones({}, true);
  stopDirectGesture();
  directFrameState.streamId = null;
}

// function resetExpressionLater(durationMs = runtimeConfig.viewer.expressionResetMs) {
//   if (expressionResetTimer) clearTimeout(expressionResetTimer);
//   expressionResetTimer = setTimeout(() => {
//     if (directFrameState.streamId) return;
//     applyDirectValues({}, true);
//     expressionResetTimer = null;
//   }, durationMs);
// }

function disconnectHub() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  hubConnectionUrl = '';
  hubRetryNoticeShown = false;
  setHubConnectionState('disconnected');
  setStatus('Disconnected');
  updateExternalStatus(null);
  log('Disconnected from hub', 'warn');
}

function connectHub() {
  const nextHubUrl = getHubUrl();
  if (eventSource && hubConnectionUrl === nextHubUrl && hubConnectionState !== 'disconnected') {
    return;
  }
  if (eventSource) disconnectHub();

  hubConnectionUrl = nextHubUrl;
  hubRetryNoticeShown = false;
  setHubConnectionState('connecting');
  const eventsUrl = `${nextHubUrl}/events?viewer_id=${encodeURIComponent(viewerId)}`;
  eventSource = new EventSource(eventsUrl);
  setStatus('Connecting...');
  updateExternalRoute();

  eventSource.onopen = () => {
    hubRetryNoticeShown = false;
    setHubConnectionState('connected');
  };

  eventSource.addEventListener('ready', async (event) => {
    const data = JSON.parse(event.data);
    hubRetryNoticeShown = false;
    setHubConnectionState('connected');
    setStatus(`Connected · viewers ${data.viewer_count}`);
    updateExternalStatus(data.snapshot?.stream || null);
    log(`Connected to hub ${nextHubUrl}`);
    await refreshRobotStatus();
    await refreshExternalStatus();
  });

  eventSource.addEventListener('stream_status', (event) => {
    const snapshot = JSON.parse(event.data);
    updateExternalStatus(snapshot);
  });

  eventSource.addEventListener('stream_frame', (event) => {
    const data = JSON.parse(event.data);
    const framePayload = data.payload || data;
    latestExternalValues = data.values || framePayload.values || {};
    updateExternalStatus(data.status || null);
    const hasBones = Object.keys(data.bones || framePayload.bones || {}).length > 0;
    const hasGesture = hasOwn(framePayload, 'gesture');
    if (!Object.keys(latestExternalValues).length && !hasBones && !hasGesture) {
      return;
    }
    lastCommandEl.textContent = `最近流式帧：${new Date().toLocaleTimeString()} / coeffs ${Object.keys(latestExternalValues).length}`;
    applyExternalFrame(framePayload);
  });

  eventSource.onerror = () => {
    setHubConnectionState('connecting');
    setStatus('Hub reconnecting...');
    if (!hubRetryNoticeShown) {
      log('Hub connection interrupted, waiting for EventSource retry', 'warn');
      hubRetryNoticeShown = true;
    }
  };
}

function resetAllControls() {
  for (const key of [...blendshapeKeys, ...jointKeys]) {
    clearManualCoefficientValue(key);
  }
  for (const control of ARM_BONE_CONTROL_DEFS) {
    if (armSliderState.has(control.key)) {
      clearManualBoneControlValue(control.key);
    }
  }
  resetDirectFrameState();
  lastCommandEl.textContent = '已重置当前数字人状态。';
}

btnConnect.addEventListener('click', connectHub);
btnDisconnect.addEventListener('click', disconnectHub);
btnRobotConnect.addEventListener('click', connectRobot);
btnRobotDisconnect.addEventListener('click', disconnectRobot);
btnReloadMapping.addEventListener('click', reloadRobotMapping);
btnForceDrag.addEventListener('click', () => {
  forceManualDrag = !forceManualDrag;
  applyForceDragState();
  applyManualValues();
  applyCombinedBoneState();
  scheduleAvatarRobotForward();
});
btnAvatarRobot.addEventListener('click', async () => {
  avatarRobotForwarding = !avatarRobotForwarding;
  applyAvatarRobotForwardingState();
  log(`Avatar robot forwarding ${avatarRobotForwarding ? 'enabled' : 'disabled'}`);

  if (avatarRobotForwardTimer) {
    clearTimeout(avatarRobotForwardTimer);
    avatarRobotForwardTimer = null;
  }

  if (avatarRobotForwarding && coefficientCatalog.coefficients.length) {
    try {
      await sendRobotBlendshapes(
        filterRobotCoefficients(getCurrentCanonicalValues(), coefficientCatalog, {
          includeDefaults: true,
        }),
        'avatar',
        { logSend: true },
      );
    } catch (error) {
      log(error.message, 'error');
    }
  }
});
btnExternalRobot.addEventListener('click', async () => {
  const nextEnabled = !externalRobotForwarding;

  try {
    await setExternalRobotForwarding(nextEnabled);
    log(`External robot forwarding ${nextEnabled ? 'enabled' : 'disabled'}`);
  } catch (error) {
    log(error.message, 'error');
  }
});
btnResetControls.addEventListener('click', resetAllControls);
hubInput.addEventListener('input', updateExternalRoute);

async function bootstrap() {
  await loadRuntimeConfig();
  await loadCoefficientCatalog();
  updateExternalStatus(null);
  applyForceDragState();

  try {
    await initAvatar();
    applyHubConnectionState();
    if (runtimeConfig.hub.autoConnectOnLoad) {
      connectHub();
    }
  } catch (error) {
    setStatus('Avatar init failed');
    log(error.message, 'error');
  }
}

bootstrap();
