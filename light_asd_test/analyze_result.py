#!/usr/bin/env python3
"""汇总打印某次 Light-ASD 推理结果：逐秒说话置信度 + 简单能量VAD对照。
用法: python analyze_result.py <videoName>   (对应 Light-ASD/demo/<videoName>/)
"""
import sys
import pickle
import numpy as np
from pathlib import Path
from scipy.io import wavfile

FPS = 25


def load_scores(demo_dir: Path):
    with open(demo_dir / "pywork" / "scores.pckl", "rb") as f:
        scores = pickle.load(f)
    if not scores:
        return None
    # 多人脸轨迹取平均，实际部署里应按 face_target 的轨迹单独取
    return [np.array(s) for s in scores]


def energy_vad(wav_path: Path, n_frames: int):
    sr, data = wavfile.read(wav_path)
    data = data.astype(np.float32) / 32768.0
    if data.ndim > 1:
        data = data.mean(axis=1)
    win = sr // FPS
    energy_db = np.array([
        20 * np.log10(np.sqrt(np.mean(data[i * win:(i + 1) * win] ** 2)) + 1e-8)
        for i in range(n_frames)
    ])
    thresh = energy_db.max() - 20
    return energy_db > thresh


def main():
    if len(sys.argv) != 2:
        print("用法: python analyze_result.py <videoName>")
        sys.exit(1)
    name = sys.argv[1]
    demo_dir = Path(__file__).parent / "Light-ASD" / "demo" / name
    if not demo_dir.exists():
        print(f"找不到 {demo_dir}，先跑一遍 Columbia_test.py")
        sys.exit(1)

    score_tracks = load_scores(demo_dir)
    if not score_tracks:
        print("未检测到任何人脸轨迹（没识别到人脸，或者脸没有在画面中稳定停留），无法评分。")
        sys.exit(0)

    wav_path = demo_dir / "pyavi" / "audio.wav"

    for ti, s in enumerate(score_tracks):
        vad = energy_vad(wav_path, len(s))
        speak_frac = (s > 0).mean()
        vad_frac = vad.mean()
        agree = ((s > 0) == vad).mean()

        print(f"\n===== 人脸轨迹 #{ti} (共{len(s)}帧, {len(s)/FPS:.1f}秒) =====")
        print(f"  ASD说话占比       : {speak_frac:6.1%}")
        print(f"  音频能量VAD占比   : {vad_frac:6.1%}   (粗略对照基准，非真实标注)")
        print(f"  逐帧判定一致率    : {agree:6.1%}")
        print(f"  分数: mean={s.mean():.2f}  max={s.max():.2f}  min={s.min():.2f}")

        print("\n  逐秒明细:")
        print(f"  {'秒':>3s} {'ASD均分':>8s} {'ASD说话':>8s} {'VAD说话':>8s}")
        for i in range(0, len(s), FPS):
            seg_s = s[i:i + FPS]
            seg_v = vad[i:i + FPS]
            mark = "  <-- 说话" if seg_s.mean() > 0 else ""
            print(f"  {i//FPS:3d} {seg_s.mean():8.2f} {seg_s.mean()>0!s:>8s} {seg_v.mean():7.0%} {mark}")


if __name__ == "__main__":
    main()
