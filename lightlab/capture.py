"""Light Lab capture: render settings tuned for comparability, then per camera an AgX PNG for looking
and a Raw linear 32-bit OpenEXR for measuring, ROI rectangles resolved from the geometry, the metrics,
and a contact sheet of the camera pictures."""

import json
import os
import time

import bpy
import numpy as np
from bpy_extras import object_utils
from mathutils import Vector

from . import metrics


def apply_render_settings(spec):
    """Fixed seed, adaptive sampling off, indirect clamp off (caustics survive), generous transmission
    bounces for light pipes. The same settings for every revision: that is what makes rows comparable."""
    scene = bpy.context.scene
    render = spec["render"]
    scene.cycles.samples = render["samples"]
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.seed = render["seed"]
    scene.cycles.use_animated_seed = False
    scene.cycles.sample_clamp_direct = 0.0
    scene.cycles.sample_clamp_indirect = 0.0
    scene.cycles.max_bounces = 24
    scene.cycles.diffuse_bounces = 4
    scene.cycles.glossy_bounces = 8
    scene.cycles.transmission_bounces = 24
    scene.cycles.transparent_max_bounces = 16
    scene.cycles.caustics_reflective = True
    scene.cycles.caustics_refractive = True
    scene.cycles.use_denoising = render["denoise"]
    scene.render.resolution_x, scene.render.resolution_y = render["resolution"]
    scene.render.resolution_percentage = render["scale"]
    scene.render.film_transparent = False


def _set_png_output(scene, path):
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGB"
    scene.render.filepath = path


def _set_exr_output(scene, path):
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "OPEN_EXR"
    settings.color_depth = "32"
    settings.color_mode = "RGB"
    scene.render.filepath = path


def _find_object(fragment):
    """The first scene object whose name contains the fragment (case-insensitive), exact match preferred."""
    want = fragment.lower()
    exact = [o for o in bpy.data.objects if o.type == "MESH" and o.name.lower() == want]
    if exact:
        return exact[0]
    for obj in bpy.data.objects:
        if obj.type == "MESH" and want in obj.name.lower():
            return obj
    return None


def resolve_rois(spec, camera_obj, warnings):
    """ROI rectangles in camera NDC ([x0, y0, x1, y1], y up). "frame" covers the whole image; "object"
    ROIs are the projected bounding box of the named body; "rect" ROIs pass through."""
    scene = bpy.context.scene
    scene.camera = camera_obj
    bpy.context.view_layer.update()
    rects = {"frame": [0.0, 0.0, 1.0, 1.0]}
    for roi in spec["rois"]:
        if roi["camera"] != camera_obj.name:
            continue
        if "rect" in roi:
            rects[roi["name"]] = roi["rect"]
            continue
        obj = _find_object(roi["object"])
        if obj is None:
            warnings.append(f"roi {roi['name']!r}: no body name contains {roi['object']!r}")
            continue
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        projected = [object_utils.world_to_camera_view(scene, camera_obj, c) for c in corners]
        xs = [min(max(p.x, 0.0), 1.0) for p in projected]
        ys = [min(max(p.y, 0.0), 1.0) for p in projected]
        if max(xs) <= 0.0 or min(xs) >= 1.0 or max(ys) <= 0.0 or min(ys) >= 1.0:
            warnings.append(f"roi {roi['name']!r}: the body is outside camera {camera_obj.name!r}")
            continue
        rects[roi["name"]] = [min(xs), min(ys), max(xs), max(ys)]
    return rects


def _exr_pixels(path):
    """A saved Raw EXR read back as a H×W×3 linear float array (row 0 = top)."""
    image = bpy.data.images.load(path)
    try:
        width, height = image.size
        channels = image.channels
        buffer = np.empty(width * height * channels, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        array = buffer.reshape(height, width, channels)[..., :3].astype(np.float64)
    finally:
        bpy.data.images.remove(image)
    return array


def _contact_sheet(paths, out_path):
    """The camera PNGs side by side (they share resolution), for one glance in STEVE."""
    images = []
    for path in paths:
        image = bpy.data.images.load(path)
        image.colorspace_settings.name = "Non-Color"   # round-trip the bytes untouched
        width, height = image.size
        buffer = np.empty(width * height * image.channels, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        images.append(buffer.reshape(height, width, image.channels))
        bpy.data.images.remove(image)
    if len(images) > 1:
        gap = np.full((images[0].shape[0], 8, images[0].shape[2]), 1.0, dtype=np.float32)
        rows = []
        for i in range(0, len(images), 2):
            row = images[i]
            for other in images[i + 1:i + 2]:
                row = np.concatenate([row, gap, other], axis=1)
            rows.append(row)
        sheet = np.concatenate(rows, axis=0) if len(rows) > 1 else rows[0]
    else:
        sheet = images[0]
    height, width = sheet.shape[:2]
    if sheet.shape[2] == 3:                       # Blender images are RGBA: pad with opaque alpha
        sheet = np.concatenate([sheet, np.ones((height, width, 1), dtype=np.float32)], axis=2)
    sheet_image = bpy.data.images.new("contact", width, height, alpha=False)
    sheet_image.colorspace_settings.name = "Non-Color"
    sheet_image.pixels.foreach_set(sheet.reshape(-1).astype(np.float32))
    sheet_image.filepath_raw = out_path
    sheet_image.file_format = "PNG"
    sheet_image.save()
    bpy.data.images.remove(sheet_image)


def render_views(spec, cameras, out_dir, on_progress=None):
    """Renders every camera, measures, writes metrics.json + study.csv + rois.json + contact.png.
    Returns (camera_stats, rois_resolved, timings, warnings)."""
    scene = bpy.context.scene
    warnings = []
    timings = {}
    camera_stats, rois_resolved = {}, {}
    png_paths = []
    view_transform = scene.view_settings.view_transform
    frame = scene.frame_start
    total = len(cameras)
    for done, (name, camera_obj) in enumerate(sorted(cameras.items())):
        if on_progress:
            on_progress(done, total, f"rendering {name}")
        t0 = time.time()
        scene.camera = camera_obj
        scene.frame_set(frame)
        rects = resolve_rois(spec, camera_obj, warnings)
        rois_resolved[name] = rects

        png_path = os.path.join(out_dir, f"{name}.png")
        _set_png_output(scene, png_path)
        bpy.ops.render.render(write_still=True)
        png_paths.append(png_path)

        exr_path = os.path.join(out_dir, f"{name}_raw.exr")
        scene.view_settings.view_transform = "Raw"      # linear values, no tone mapping
        _set_exr_output(scene, exr_path)
        bpy.ops.render.render(write_still=True)
        scene.view_settings.view_transform = view_transform

        rgb = _exr_pixels(exr_path)
        camera_stats[name] = {roi: metrics.stats_from_array(rgb, rect) for roi, rect in rects.items()}
        timings[name] = round(time.time() - t0, 2)
    if on_progress:
        on_progress(total, total, "measuring")

    rois_name = "rois.json"
    with open(os.path.join(out_dir, rois_name), "w", encoding="utf-8") as f:
        json.dump(rois_resolved, f, indent=2)
    contact_name = "contact.png"
    _contact_sheet(png_paths, os.path.join(out_dir, contact_name))
    return camera_stats, rois_resolved, timings, warnings, rois_name, contact_name
