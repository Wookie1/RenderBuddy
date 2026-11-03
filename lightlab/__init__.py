"""Light Lab: light-transmission studies inside RenderBuddy's Blender.

A generated main.py calls run_study(spec) with the validated study spec (see LIGHTLAB.md). The scene
comes from the linked designs (`from links import <alias>`, the same machinery as scene scripts) or from
the spec's demo geometry. Every camera renders twice: an AgX PNG for looking and a Raw linear 32-bit
OpenEXR for measuring. Metrics go to metrics.json and one study.csv row per camera × ROI.

Only `spec` and `metrics` are imported at package level: both are bpy-free, so a spec can be validated
before Blender starts (renderbuddy.py does) and a future Mitsuba adapter can reuse the metrics."""

import os
import sys
import time

import bbstate

from . import spec as spec_mod
from .spec import SpecError


def _load_copies():
    """Adds every linked design to the scene; returns {alias: {instances: {path: obj}}}."""
    links = sys.modules.get("links")
    if links is None:
        return {}
    copies = {}
    for alias in sorted(name for name in dir(links) if not name.startswith("_")):
        link = getattr(links, alias)
        if not callable(link):
            continue
        coll = link()
        copies[alias] = {"instances": link.instances(coll)}
    return copies


def run_study(raw_spec):
    """The whole study: scene, renders, metrics, outputs. Called inside Blender by a generated main.py."""
    from . import scene, capture, demo        # bpy-dependent: only inside Blender

    t0 = time.time()
    spec = spec_mod.normalize(raw_spec)
    warnings = []

    if spec["demo"]:
        copies = {"demo": {"instances": demo.build()}}
    else:
        copies = _load_copies()
        if not copies:
            raise SpecError("No designs were sent with the run and the spec does not set \"demo\": true.")

    built = scene.build(spec, copies)
    warnings += built["warnings"]
    def progress(done, total, stage):
        bbstate.progress(done, total, stage)

    camera_stats, rois_resolved, timings, capture_warnings, rois_name, contact_name, actual = \
        capture.render_views(spec, built["cameras"], bbstate.OUT, progress)
    warnings += capture_warnings

    from datetime import datetime, timezone
    meta = {"study": spec["study"], "revision": spec["revision"], "spec_hash": spec["spec_hash"],
            "samples": actual["samples"], "seed": actual["seed"],
            "device": bbstate.DEVICE, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    metrics_name, csv_name = metrics.write_results(bbstate.OUT, camera_stats, rois_resolved, meta)

    for name in sorted(camera_stats):
        for suffix in (".png", "_raw.exr"):
            bbstate.output(os.path.join(bbstate.OUT, f"{name}{suffix}"))
    for name in (rois_name, metrics_name, csv_name, contact_name):
        bbstate.output(os.path.join(bbstate.OUT, name))

    total = round(time.time() - t0, 2)
    bbstate.DETAILS["lightlab"] = {
        "study": spec["study"], "revision": spec["revision"], "spec_hash": spec["spec_hash"],
        "demo": spec["demo"], "cameras": sorted(camera_stats),
        "rois": sorted({roi for per_camera in camera_stats.values() for roi in per_camera}),
        "samples": actual["samples"], "seed": actual["seed"],
        "resolution": actual["resolution"], "denoise": actual["denoise"],
        "seconds": total, "render_seconds": timings,
    }
    bbstate.WARNINGS.extend(warnings)
    return {"metrics": metrics_name, "csv": csv_name, "seconds": total}
