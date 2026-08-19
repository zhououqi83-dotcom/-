import { describe, expect, it } from 'vitest';

import { normalizeCoefficientCatalog } from '../src/blendshapeMapping.js';
import { buildExternalFrame } from '../src/externalProtocol.js';

const coefficientCatalog = normalizeCoefficientCatalog({
  coefficients: {
    MouthSmileLeft: {
      min: 0,
      max: 1,
      targets: {},
    },
    HeadPitch: {
      min: -1,
      max: 1,
      targets: {},
    },
  },
});

describe('buildExternalFrame', () => {
  it('keeps gesture and bones while normalizing canonical values', () => {
    const frame = buildExternalFrame(
      {
        stream_id: 'stream-wave-right',
        motion: 'wave_right',
        t_ms: 660,
        duration_ms: 66,
        is_first: false,
        is_last: false,
        values: {
          mouthSmileLeft: 0.5,
          HeadPitch: -0.25,
        },
        bones: {
          RightHand: {
            quaternion: [0, 0.1, 0.2],
          },
        },
        gesture: ['handup', null, true],
      },
      { coefficientCatalog },
    );

    expect(frame.values).toEqual({
      MouthSmileLeft: 0.5,
      HeadPitch: -0.25,
    });
    expect(frame.bones).toEqual({
      RightHand: {
        quaternion: [0, 0.1, 0.2],
      },
    });
    expect(frame.gesture).toEqual(['handup', null, true]);
    expect(frame.stream_id).toBe('stream-wave-right');
    expect(frame.motion).toBe('wave_right');
    expect(frame.t_ms).toBe(660);
    expect(frame.duration_ms).toBe(66);
    expect(frame.is_first).toBe(false);
    expect(frame.is_last).toBe(false);
    expect(frame.reset_missing).toBe(true);
  });

  it('omits gesture when the sender does not provide one', () => {
    const frame = buildExternalFrame(
      {
        values: {
          MouthSmileLeft: 0.3,
        },
      },
      { coefficientCatalog },
    );

    expect(Object.prototype.hasOwnProperty.call(frame, 'gesture')).toBe(false);
  });

  it('rejects malformed gestures', () => {
    expect(() => buildExternalFrame(
      {
        gesture: [null],
      },
      { coefficientCatalog },
    )).toThrow(/gesture/);
  });
});
