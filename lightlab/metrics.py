"""Light Lab metrics: luminance statistics over regions of interest, written to metrics.json and one
study.csv row per camera × ROI for revision-to-revision comparison.

stats_from_array is bpy-free (numpy only): a future Mitsuba adapter feeds its own arrays into it.
See LIGHTLAB.md for the metric definitions and the comparability conventions."""

import csv
import json
import os

import numpy as np

# Relative luminance of linear sRGB (Rec. 709)
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)

CSV_FIELDS = ["study", "revision", "camera", "roi", "mean", "median", "p10", "p90", "p95", "p99",
              "max", "min", "cov", "uniformity", "hotspot", "samples", "seed", "device",
              "spec_hash", "timestamp"]


def stats_from_array(rgb, rect):
    """Statistics of one ROI. rgb: H×W×3 linear float array; rect: [x0, y0, x1, y1] normalized,
    y measured up from the bottom (camera NDC, the same convention as Blender's world_to_camera_view).
    Returns None when the rect misses the image."""
    height, width = rgb.shape[:2]
    x0, x1 = int(round(rect[0] * width)), int(round(rect[2] * width))
    y0, y1 = int(round(rect[1] * height)), int(round(rect[3] * height))
    patch = rgb[max(height - y1, 0):max(height - y0, 0), max(x0, 0):max(x1, 0)]
    if patch.size == 0:
        return None
    y = patch @ LUMA
    mean = float(y.mean())
    p = np.percentile(y, [10, 90, 95, 99])
    out = {
        "count": int(y.size),
        "mean": mean,
        "median": float(np.median(y)),
        "p10": float(p[0]), "p90": float(p[1]), "p95": float(p[2]), "p99": float(p[3]),
        "max": float(y.max()), "min": float(y.min()),
        "r_mean": float(patch[..., 0].mean()),
        "g_mean": float(patch[..., 1].mean()),
        "b_mean": float(patch[..., 2].mean()),
    }
    out["std"] = float(y.std())
    out["cov"] = out["std"] / mean if mean > 1e-12 else None                  # coefficient of variation
    out["uniformity"] = p[0] / p[1] if p[1] > 1e-12 else None                 # p10 / p90, 1 = perfectly even
    out["hotspot"] = p[3] / float(np.median(y)) if mean > 1e-12 else None     # p99 / median
    return out


def write_results(out_dir, camera_stats, rois_resolved, meta):
    """Writes metrics.json and study.csv; returns their file names."""
    document = {**meta, "cameras": {}}
    for camera, per_roi in camera_stats.items():
        document["cameras"][camera] = {"rois": per_roi}
    document["rois_resolved"] = rois_resolved
    metrics_name = "metrics.json"
    with open(os.path.join(out_dir, metrics_name), "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)

    csv_name = "study.csv"
    row_base = {k: meta.get(k) for k in ("study", "revision", "samples", "seed", "device", "spec_hash")}
    row_base["timestamp"] = meta.get("timestamp")
    write_header = not os.path.exists(os.path.join(out_dir, csv_name))
    with open(os.path.join(out_dir, csv_name), "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for camera, per_roi in camera_stats.items():
            for roi, stats in per_roi.items():
                if stats is None:
                    continue
                row = {**row_base, "camera": camera, "roi": roi}
                row.update({k: stats.get(k) for k in CSV_FIELDS
                            if k not in ("study", "revision", "camera", "roi", "samples", "seed", "device",
                                         "spec_hash", "timestamp")})
                writer.writerow(row)
    return metrics_name, csv_name
