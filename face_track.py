"""Local CPU face-tracking crop planner for vertical Shorts.

Uses OpenCV YuNet (offline ONNX model, no cloud APIs) to locate a prominent
face, smooths the horizontal crop trajectory with deadband and hard-cut
snapping, and emits a bounded, flat FFmpeg crop filter with transparent
fallback to center_crop.
"""
from __future__ import annotations

import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
YUNET_MODEL = ROOT / "models" / "face-detect" / "face_detection_yunet_2023mar.onnx"


def even_floor(value: float) -> int:
    n = int(math.floor(value))
    return max(2, n - (n % 2))


def compute_crop_size(src_w: int, src_h: int, target_w: int, target_h: int) -> tuple[int, int]:
    """Return even (crop_w, crop_h) inside source bounds matching target aspect ratio."""
    if src_w < 2 or src_h < 2 or target_w < 2 or target_h < 2:
        raise ValueError("Dimensions must be positive integers >= 2")
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio >= target_ratio:
        crop_h = even_floor(src_h)
        crop_w = min(even_floor(src_w), even_floor(crop_h * target_ratio))
    else:
        crop_w = even_floor(src_w)
        crop_h = min(even_floor(src_h), even_floor(crop_w / target_ratio))
    return crop_w, crop_h


def smooth_positions(
    samples: list[tuple[float, float | None, float]],
    default_x: float,
    max_x: float,
    *,
    alpha: float = 0.28,
    deadband: float = 16.0,
    cut_threshold: float = 32.0,
    max_gap_seconds: float = 1.5,
) -> list[tuple[float, float]]:
    """Smooth raw (t, target_x | None, frame_diff) samples into stable crop X positions."""
    if not samples:
        return []
    first_detected = next((x for _, x, _ in samples if x is not None), default_x)
    current = max(0.0, min(max_x, first_detected))
    last_seen_t = samples[0][0]
    smoothed: list[tuple[float, float]] = []

    for t, raw_x, diff in samples:
        is_cut = diff >= cut_threshold
        if raw_x is not None:
            target = max(0.0, min(max_x, raw_x))
            last_seen_t = t
            if is_cut:
                current = target
            elif abs(target - current) >= deadband:
                current = current + alpha * (target - current)
        else:
            if t - last_seen_t > max_gap_seconds:
                if abs(default_x - current) >= deadband:
                    current = current + (alpha * 0.5) * (default_x - current)
        current = max(0.0, min(max_x, current))
        smoothed.append((round(t, 3), round(current, 1)))
    return smoothed


def compress_keyframes(
    points: list[tuple[float, float]],
    *,
    tolerance: float = 12.0,
    max_segments: int = 36,
) -> list[tuple[float, float]]:
    """Reduce dense (t, x) samples to a compact piecewise-linear keyframe list."""
    if len(points) <= 2:
        return list(points)

    def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
        kept = [pts[0]]
        anchor_idx = 0
        for i in range(1, len(pts) - 1):
            t0, x0 = pts[anchor_idx]
            t2, x2 = pts[i + 1]
            ti, xi = pts[i]
            if t2 <= t0:
                continue
            interp = x0 + (x2 - x0) * ((ti - t0) / (t2 - t0))
            if abs(xi - interp) > tol or abs(xi - kept[-1][1]) > tol * 2.5:
                kept.append(pts[i])
                anchor_idx = i
        kept.append(pts[-1])
        return kept

    tol = tolerance
    reduced = simplify(points, tol)
    while len(reduced) - 1 > max_segments and tol < 400.0:
        tol *= 1.5
        reduced = simplify(points, tol)
    if len(reduced) - 1 > max_segments:
        step = max(1, math.ceil((len(reduced) - 1) / max_segments))
        reduced = reduced[::step]
        if reduced[-1] != points[-1]:
            reduced.append(points[-1])
    return reduced


def build_crop_filter(
    crop_w: int,
    crop_h: int,
    crop_y: int,
    max_x: int,
    keyframes: list[tuple[float, float]],
    target_w: int,
    target_h: int,
) -> tuple[str, str]:
    """Return (mode, ffmpeg_filter) using a flat, bounded crop expression."""
    if not keyframes or max_x <= 0:
        x = max(0, min(max_x, round(max_x / 2)))
        return "fallback_center", f"crop={crop_w}:{crop_h}:{x}:{crop_y},scale={target_w}:{target_h},setsar=1"

    xs = [x for _, x in keyframes]
    if max(xs) - min(xs) < 20.0 or len(keyframes) == 1:
        x = max(0, min(max_x, round(statistics.median(xs))))
        return "tracked_static", f"crop={crop_w}:{crop_h}:{x}:{crop_y},scale={target_w}:{target_h},setsar=1"

    terms: list[str] = []
    first_t, first_x = keyframes[0]
    if first_t > 0:
        terms.append(f"lt(t,{first_t:.3f})*{first_x:.1f}")
    for (t0, x0), (t1, x1) in zip(keyframes, keyframes[1:]):
        dt = t1 - t0
        if dt <= 1e-3:
            continue
        slope = (x1 - x0) / dt
        if abs(slope) < 0.5:
            expr = f"{x0:.1f}"
        else:
            expr = f"({x0:.1f}+({slope:.2f})*(t-{t0:.3f}))"
        terms.append(f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*{expr}")
    last_t, last_x = keyframes[-1]
    terms.append(f"gte(t,{last_t:.3f})*{last_x:.1f}")
    x_expr = f"clip({'+'.join(terms)},0,{max_x})"
    return (
        "tracked_pan",
        f"crop={crop_w}:{crop_h}:'{x_expr}':{crop_y},scale={target_w}:{target_h},setsar=1",
    )


def _select_face(faces, prev_cx: float | None, frame_w: int) -> float | None:
    if not faces:
        return None
    best_cx = None
    best_score = -1.0
    for fx, _fy, fw, fh, conf in faces:
        cx = fx + fw / 2.0
        area = float(fw * fh)
        proximity = 1.0
        if prev_cx is not None and frame_w > 0:
            dist = abs(cx - prev_cx) / frame_w
            proximity = max(0.65, 1.15 - dist * 0.6)
        score = area * float(conf) * proximity
        if score > best_score:
            best_score = score
            best_cx = cx
    return best_cx


def _center_fallback(target_width: int, target_height: int, reason: str) -> dict:
    return {
        "mode": "fallback_center",
        "reason": reason,
        "sampled_frames": 0,
        "faces_detected": 0,
        "keyframes": [],
        "filter": (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=2,crop={target_width}:{target_height},setsar=1"
        ),
    }


def analyze_video(
    video_path: Path,
    target_width: int,
    target_height: int,
    *,
    sample_fps: float = 2.0,
    model_path: Path | None = None,
) -> dict:
    """Detect and track prominent faces across a clip and return a crop tracking plan."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        return _center_fallback(target_width, target_height, f"opencv unavailable ({exc})")

    detector_model = Path(model_path) if model_path else YUNET_MODEL
    if not detector_model.is_file() or not hasattr(cv2, "FaceDetectorYN"):
        return _center_fallback(target_width, target_height, f"YuNet model unavailable at {detector_model}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return _center_fallback(target_width, target_height, "unable to open clip for face tracking")

    try:
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        if fps <= 0 or fps > 240:
            fps = 30.0
        if src_w < 2 or src_h < 2:
            return _center_fallback(target_width, target_height, "invalid source dimensions")

        crop_w, crop_h = compute_crop_size(src_w, src_h, target_width, target_height)
        crop_y = max(0, ((src_h - crop_h) // 2) // 2 * 2)
        max_x = max(0, src_w - crop_w)
        default_x = max_x / 2.0

        if max_x < 4:
            return {
                "mode": "fallback_center",
                "reason": "source already matches or is narrower than target aspect ratio",
                "src_width": src_w,
                "src_height": src_h,
                "crop_width": crop_w,
                "crop_height": crop_h,
                "sampled_frames": 0,
                "faces_detected": 0,
                "keyframes": [],
                "filter": f"crop={crop_w}:{crop_h}:0:{crop_y},scale={target_width}:{target_height},setsar=1",
            }

        scale_down = min(1.0, 480.0 / max(1, src_w))
        small_w = max(2, round(src_w * scale_down))
        small_h = max(2, round(src_h * scale_down))
        detector = cv2.FaceDetectorYN.create(
            str(detector_model),
            "",
            (small_w, small_h),
            score_threshold=0.55,
            nms_threshold=0.3,
            top_k=20,
        )

        step = max(1, round(fps / max(0.5, sample_fps)))
        min_face = max(18, int(min(small_w, small_h) * 0.06))
        samples: list[tuple[float, float | None, float]] = []
        prev_gray = None
        prev_cx = None
        faces_detected = 0
        frame_idx = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % step == 0:
                t = frame_idx / fps
                small = (
                    cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
                    if scale_down < 1.0
                    else frame
                )
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                diff = float(np.mean(cv2.absdiff(gray, prev_gray))) if prev_gray is not None else 0.0
                prev_gray = gray

                _, raw_faces = detector.detect(small)
                valid_faces = []
                if raw_faces is not None:
                    for row in raw_faces:
                        fx, fy, fw, fh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                        conf = float(row[-1])
                        if fw >= min_face and fh >= min_face:
                            valid_faces.append((fx, fy, fw, fh, conf))

                cx_small = _select_face(valid_faces, prev_cx, small_w)
                if cx_small is not None:
                    faces_detected += 1
                    prev_cx = cx_small
                    cx_src = cx_small / scale_down
                    target_x = max(0.0, min(float(max_x), cx_src - crop_w / 2.0))
                else:
                    target_x = None
                samples.append((t, target_x, diff))
            frame_idx += 1
    finally:
        cap.release()

    if faces_detected == 0:
        x = round(default_x)
        return {
            "mode": "fallback_center",
            "reason": "no faces detected in clip",
            "src_width": src_w,
            "src_height": src_h,
            "crop_width": crop_w,
            "crop_height": crop_h,
            "sampled_frames": len(samples),
            "faces_detected": 0,
            "keyframes": [],
            "filter": f"crop={crop_w}:{crop_h}:{x}:{crop_y},scale={target_width}:{target_height},setsar=1",
        }

    smoothed = smooth_positions(samples, default_x, float(max_x))
    keyframes = compress_keyframes(smoothed)
    mode, flt = build_crop_filter(crop_w, crop_h, crop_y, max_x, keyframes, target_width, target_height)
    return {
        "mode": mode,
        "src_width": src_w,
        "src_height": src_h,
        "crop_width": crop_w,
        "crop_height": crop_h,
        "sampled_frames": len(samples),
        "faces_detected": faces_detected,
        "keyframes": keyframes,
        "filter": flt,
    }
