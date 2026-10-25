"""RenderBuddy's marketing kit: branded product images and videos from STEVE designs, made in the local Blender.

Usable piece by piece from any render_scene script (`import bbkit`), or all at once with make_kit(), which the
connector's marketing_kit action runs:

    brand = bbkit.Brand(SAMPLE_BRAND)                    # name, colours, font, words
    studio = bbkit.Studio(objects, brand, look="dark")   # lights, floor and camera for a set of objects
    studio.frame(width, height, window=(0.4, 0.05, 0.98, 0.95))   # fit them into part of the frame
    studio.render_png(path, width, height, transparent=True)
    layout = bbkit.Layout(width, height)                 # 2D layout in Blender's video editor
    layout.color(brand.ink).image(path, ...).text("Headline", ...).render_png(path)

Everything is in pixels from the top-left of the frame. Colours are "#RRGGBB" strings.
"""
import json
import math
import os
import time

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

import bbstate

SAMPLE_BRAND = {
    "name": "inktap",
    "tagline": "Menus that say thanks.",
    "headline": "The menu stand that takes tips.",
    "subhead": "An ePaper menu with a glowing NFC tip tag. Guests tap their phone, staff get thanked.",
    "cta": "Order a sample at inktap.example",
    "url": "inktap.example",
    "captions": ["Your menu, on ePaper.", "Tap to tip.", "Built to be serviced."],
    "reel_captions": ["Tap.", "Tip.", "Done."],
    "colors": {"ink": "#17181B", "paper": "#F3EEE5", "accent": "#E9A23B", "slate": "#5B6470"},
    "font": "Inter",
}


# Colours and fonts -----------------------------------------------------------------------------------------------------

def srgb(hex_color, alpha=1.0):
    """'#RRGGBB' as sRGB floats (what text and colour strips take)."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)


def linear(hex_color, alpha=1.0):
    """'#RRGGBB' as scene-linear floats (what materials and the world take)."""
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b, _ = srgb(hex_color)
    return (lin(r), lin(g), lin(b), alpha)


def font(name="Inter"):
    """A font: one of Blender's own (Inter, DejaVuSansMono) or a font file's path."""
    path = name
    if not os.path.isfile(path):
        folder = bpy.utils.system_resource("DATAFILES", path="fonts")
        path = os.path.join(folder, name if name.endswith((".woff2", ".ttf", ".otf")) else name + ".woff2")
    if not os.path.isfile(path):
        return bpy.data.fonts.load("<builtin>")
    for existing in bpy.data.fonts:
        if existing.filepath == path:
            return existing
    return bpy.data.fonts.load(path)


class Brand:
    def __init__(self, spec=None):
        spec = dict(SAMPLE_BRAND, **(spec or {}))
        spec["colors"] = dict(SAMPLE_BRAND["colors"], **(spec.get("colors") or {}))
        self.spec = spec
        self.name = spec["name"]
        self.colors = spec["colors"]
        self.ink, self.paper = self.colors["ink"], self.colors["paper"]
        self.accent, self.slate = self.colors["accent"], self.colors["slate"]

    def __getitem__(self, key):
        return self.spec.get(key)

    @property
    def font(self):
        return font(self.spec.get("font") or "Inter")


def balance(words, longest=24):
    """Break a headline into lines of about equal length (no single word left on its own line)."""
    parts = words.split()
    if len(words) <= longest or len(parts) < 3:
        return words
    lines = max(2, math.ceil(len(words) / longest))
    target = len(words) / lines
    out, line = [], ""
    for word in parts:
        if line and len(line) + 1 + len(word) > target + 4 and len(out) < lines - 1:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    return "\n".join(out)


# Points and framing ----------------------------------------------------------------------------------------------------

def mesh_objects(objects):
    return [o for o in objects if o.type == "MESH" and not o.hide_render]


def world_points(objects, frames=None):
    """The world-space corners of these objects' bounding boxes, at each of `frames` (default: the current one)."""
    scene = bpy.context.scene
    keep = scene.frame_current
    points = []
    for frame in frames or [keep]:
        scene.frame_set(frame)
        for obj in mesh_objects(objects):
            mw = obj.matrix_world
            points += [mw @ Vector(c) for c in obj.bound_box]
    scene.frame_set(keep)
    return points


def bounds(points):
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return lo, hi


# 3D studio -------------------------------------------------------------------------------------------------------------

_CALIBRATED = {}


def world_color_for(hex_color, like_scene):
    """The world colour that renders as `hex_color` on screen with like_scene's view transform and look (found by
    rendering a few tiny frames of nothing but the world)."""
    key = (hex_color, like_scene.view_settings.view_transform, like_scene.view_settings.look)
    if key in _CALIBRATED:
        return _CALIBRATED[key]
    target = srgb(hex_color)[:3]

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    goal = [lin(c) for c in target]
    scene = bpy.data.scenes.new("Calibrate")
    cam = bpy.data.objects.new("Calibrate camera", bpy.data.cameras.new("Calibrate camera"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    world = bpy.data.worlds.new("Calibrate")
    world.use_nodes = True
    scene.world = world
    bg = world.node_tree.nodes["Background"]
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.device = like_scene.cycles.device
    scene.render.resolution_x = scene.render.resolution_y = 4
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = like_scene.view_settings.view_transform
    scene.view_settings.look = like_scene.view_settings.look
    scene.render.image_settings.file_format = "PNG"
    color = list(goal)
    path = bbstate.scratch("calibrate.png")
    for _ in range(4):
        bg.inputs["Color"].default_value = (*color, 1)
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True, scene=scene.name)
        img = bpy.data.images.load(path, check_existing=False)
        px = list(img.pixels[:4])
        bpy.data.images.remove(img)
        got = [lin(c) for c in px[:3]]
        color = [min(c * g / max(m, 1e-5), 50.0) for c, g, m in zip(color, goal, got)]
    bpy.data.scenes.remove(scene)
    _CALIBRATED[key] = (*color, 1.0)
    return _CALIBRATED[key]


class Studio:
    """Lights, a shadow-catching floor and a camera around a set of objects, in the brand's look.

    look: "dark" (ink world, studio lights), "light" (paper world, softer, brighter). Renders can be transparent, for
    layouts over the exact brand colours."""

    def __init__(self, objects, brand, look="dark", points=None, floor_z=None, scene=None):
        self.scene = scene or bpy.context.scene
        self.brand, self.look = brand, look
        self.objects = list(objects)
        self.points = points or world_points(self.objects)
        lo, hi = bounds(self.points)
        self.centre, self.size = (lo + hi) / 2, (hi - lo).length
        floor_z = lo.z if floor_z is None else floor_z
        self.made = []
        scene = self.scene
        coll = bpy.data.collections.new("Studio")
        scene.collection.children.link(coll)
        self.collection = coll

        mesh = bpy.data.meshes.new("Studio floor")
        r = self.size * 15
        mesh.from_pydata([(-r, -r, 0), (r, -r, 0), (r, r, 0), (-r, r, 0)], [], [(0, 1, 2, 3)])
        floor = bpy.data.objects.new("Studio floor", mesh)
        floor.location = (self.centre.x, self.centre.y, floor_z)
        floor.is_shadow_catcher = True
        coll.objects.link(floor)
        self.floor = floor

        self.target = bpy.data.objects.new("Camera target", None)
        coll.objects.link(self.target)
        self.target.location = self.centre
        self.camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        coll.objects.link(self.camera)
        self.camera.data.lens = 60
        self.camera.data.clip_start, self.camera.data.clip_end = 1, 100_000
        aim = self.camera.constraints.new("TRACK_TO")
        aim.target, aim.track_axis, aim.up_axis = self.target, "TRACK_NEGATIVE_Z", "UP_Y"
        scene.camera = self.camera

        dark = look == "dark"
        self.light("Key", (1.0, -0.9, 1.1), 8 if dark else 7, 0.6)
        self.light("Fill", (-1.1, -0.6, 0.5), 2 if dark else 3.5, 1.0)
        self.light("Rim", (0.0, 1.1, 0.9), 4 if dark else 2.5, 0.4)
        world = bpy.data.worlds.new("Studio")
        world.use_nodes = True
        background = world.node_tree.nodes["Background"]
        scene.world = world
        for look_name in ("AgX - Medium High Contrast", "Medium High Contrast"):
            try:
                scene.view_settings.look = look_name
                break
            except TypeError:
                pass
        # The background shows the brand colour exactly, through the view transform, so videos match layouts.
        background.inputs["Color"].default_value = world_color_for(brand.ink if dark else brand.paper, scene)
        background.inputs["Strength"].default_value = 1.0

    def light(self, name, offset, strength, size):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
        self.collection.objects.link(light)
        light.location = self.centre + Vector(offset) * self.size
        light.rotation_euler = (self.centre - light.location).to_track_quat("-Z", "Y").to_euler()
        light.data.energy = strength * (self.centre - light.location).length ** 2
        light.data.size = size * self.size
        return light

    def frame(self, width, height, direction=(0.75, -1.0, 0.55), window=(0.0, 0.0, 1.0, 1.0), margin=1.08,
              points=None, lens=None, frame=None):
        """Aim the camera along `direction` (from the subject towards the camera) at the subject's centre, at the
        distance that fits every point into `window` (left, top, right, bottom as fractions of the frame), keyed at
        `frame` when given. Returns the camera location."""
        cam = self.camera.data
        if lens:
            cam.lens = lens
        points = points or self.points
        lo, hi = bounds(points)
        centre = (lo + hi) / 2
        self.target.location = centre
        d = Vector(direction).normalized()
        right = d.cross(Vector((0, 0, 1)))
        right = right.normalized() if right.length > 1e-6 else Vector((1, 0, 0))
        right = -right
        up = right.cross(-d).normalized()
        big = max(width, height)
        tan_big = (cam.sensor_width / 2) / cam.lens
        u0, v0, u1, v1 = window
        tx = tan_big * width / big * (u1 - u0)
        ty = tan_big * height / big * (v1 - v0)
        cam.shift_x = (0.5 - (u0 + u1) / 2) * width / big
        cam.shift_y = ((v0 + v1) / 2 - 0.5) * height / big
        distance = 0.0
        for p in points:
            rel = p - centre
            depth = rel.dot(d)
            distance = max(distance, depth + margin * abs(rel.dot(right)) / tx,
                           depth + margin * abs(rel.dot(up)) / ty)
        self.camera.location = centre + d * distance
        if frame is not None:
            self.camera.keyframe_insert("location", frame=frame)
            self.target.keyframe_insert("location", frame=frame)
        return self.camera.location.copy()

    def project(self, point, width, height):
        """A world point's pixel position (x from the left, y from the top) in a width x height render."""
        scene = self.scene
        scene.render.resolution_x, scene.render.resolution_y = width, height
        bpy.context.view_layer.update()
        co = world_to_camera_view(scene, self.camera, Vector(point))
        return co.x * width, (1 - co.y) * height

    def _settings(self, width, height, samples, transparent):
        scene = self.scene
        scene.camera = self.camera
        scene.render.engine = "CYCLES"
        scene.render.resolution_x, scene.render.resolution_y = width, height
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = transparent
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        scene.cycles.device = "GPU" if getattr(bbstate, "DEVICE", "CPU") != "CPU" else "CPU"
        scene.view_settings.view_transform = "AgX"

    def render_png(self, path, width, height, samples=32, transparent=False, frame=None):
        self._settings(width, height, samples, transparent)
        scene = self.scene
        if frame is not None:
            scene.frame_set(frame)
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        return path

    def render_mp4(self, path, width, height, start, end, samples=16, stage="video"):
        """Frames start..end as a near-lossless MP4 (for laying out afterwards)."""
        self._settings(width, height, samples, False)
        scene = self.scene
        scene.frame_start, scene.frame_end = start, end
        _mp4(scene, path, lossless=True)
        _render_animation(scene, stage)
        return _finished_mp4(path)


def _mp4(scene, path, lossless=False):
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "PERC_LOSSLESS" if lossless else "HIGH"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.ffmpeg.audio_codec = "NONE"
    scene.render.filepath = path


def _render_animation(scene, stage):
    total = len(range(scene.frame_start, scene.frame_end + 1, scene.frame_step))
    done = [0]

    def after(*_):
        done[0] += 1
        bbstate.progress(done[0], total, stage)
    bpy.app.handlers.render_post.append(after)
    try:
        bpy.ops.render.render(animation=True)
    finally:
        bpy.app.handlers.render_post.remove(after)


def _finished_mp4(path):
    """Blender may append frame numbers to a video's name: give it back its own."""
    folder, base = os.path.split(path)
    stem = os.path.splitext(base)[0]
    if not os.path.exists(path):
        for name in sorted(os.listdir(folder)):
            if name.startswith(stem) and name.endswith(".mp4"):
                os.replace(os.path.join(folder, name), path)
                break
    return path


# 2D layout in the video editor -----------------------------------------------------------------------------------------

class Layout:
    """A picture or video laid out in its own scene with Blender's video editor: colour panels, images, videos and
    text, each from `start` for `length` frames (default: all of it). Pixels from the top-left; colours '#RRGGBB'."""

    def __init__(self, width, height, frames=1, fps=24, name="Layout"):
        self.scene = bpy.data.scenes.new(name)
        self.w, self.h, self.frames = width, height, frames
        scene = self.scene
        scene.render.resolution_x, scene.render.resolution_y = width, height
        scene.render.resolution_percentage = 100
        scene.render.fps, scene.render.fps_base = fps, 1
        scene.frame_start, scene.frame_end = 1, frames
        scene.render.use_sequencer = True
        scene.render.use_compositing = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        self.editor = scene.sequence_editor_create()
        self.channel = 0
        self.count = 0

    def _place(self, strip, x, y, w, h, start, length, fade=0):
        strip.transform.offset_x = x + w / 2 - self.w / 2
        strip.transform.offset_y = -(y + h / 2 - self.h / 2)
        if strip.type != "TEXT":
            strip.blend_type = "ALPHA_OVER"
        self._fade(strip, start, length, fade)
        return strip

    def _fade(self, strip, start, length, fade):
        """fade: frames to fade in and out, or (in, out); 0 for none."""
        fade_in, fade_out = fade if isinstance(fade, (tuple, list)) else (fade, fade)
        if not fade_in and not fade_out:
            return
        keys = []
        if fade_in and start > 1:
            keys += [(start, 0.0), (start + fade_in, 1.0)]
        if fade_out and start + length <= self.frames:
            keys += [(start + length - fade_out, 1.0), (start + length, 0.0)]
        for frame, alpha in keys:
            strip.blend_alpha = alpha
            strip.keyframe_insert("blend_alpha", frame=frame)

    def _span(self, start, length):
        start = start or 1
        length = length or (self.frames - start + 1)
        self.channel += 1
        self.count += 1
        return start, length, self.channel, f"s{self.count}"

    def color(self, hex_color, x=0, y=0, w=None, h=None, start=None, length=None, fade=0, alpha=1.0):
        start, length, channel, name = self._span(start, length)
        w, h = w if w is not None else self.w, h if h is not None else self.h
        strip = self.editor.strips.new_effect(name, "COLOR", channel, start, length=length)
        strip.color = srgb(hex_color)[:3]
        strip.transform.scale_x, strip.transform.scale_y = w / self.w, h / self.h
        if alpha < 1 and not fade:
            strip.blend_alpha = alpha
        self._place(strip, x, y, w, h, start, length, fade)
        return self

    def line(self, x0, y0, x1, y1, hex_color, thickness=3, start=None, length=None, fade=0):
        """A straight line between two pixel points."""
        start, length, channel, name = self._span(start, length)
        strip = self.editor.strips.new_effect(name, "COLOR", channel, start, length=length)
        strip.color = srgb(hex_color)[:3]
        dx, dy = x1 - x0, y1 - y0
        long = max(math.hypot(dx, dy), 1)
        strip.transform.scale_x, strip.transform.scale_y = long / self.w, thickness / self.h
        strip.transform.rotation = -math.atan2(dy, dx)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self._place(strip, cx - 0.5, cy - 0.5, 1, 1, start, length, fade)
        return self

    def image(self, path, x=0, y=0, w=None, h=None, fit="contain", start=None, length=None, fade=0,
              align=(0.5, 0.5)):
        """An image fitted into the box: contain (all of it shows) or cover (fills the box, trimmed)."""
        start, length, channel, name = self._span(start, length)
        w, h = w if w is not None else self.w, h if h is not None else self.h
        img = bpy.data.images.load(path, check_existing=True)
        iw, ih = img.size
        strip = self.editor.strips.new_image(name, path, channel, start, fit_method="ORIGINAL")
        strip.frame_final_duration = length
        scale = (min if fit == "contain" else max)(w / iw, h / ih)
        strip.transform.scale_x = strip.transform.scale_y = scale
        shown_w, shown_h = iw * scale, ih * scale
        if fit == "cover":
            cut_x, cut_y = (shown_w - w) / scale, (shown_h - h) / scale
            strip.crop.min_x = int(cut_x * align[0])
            strip.crop.max_x = int(cut_x * (1 - align[0]))
            strip.crop.max_y = int(cut_y * align[1])
            strip.crop.min_y = int(cut_y * (1 - align[1]))
            shown_w, shown_h = w, h
            ox = (strip.crop.min_x - strip.crop.max_x) * scale / 2
            oy = (strip.crop.min_y - strip.crop.max_y) * scale / 2
        else:
            ox = oy = 0
        bx = x + (w - shown_w) * align[0]
        by = y + (h - shown_h) * align[1]
        self._place(strip, bx, by, shown_w, shown_h, start, length, fade)
        strip.transform.offset_x -= ox
        strip.transform.offset_y -= oy
        return self

    def movie(self, path, x=0, y=0, w=None, h=None, start=None, length=None, fade=0, offset=0):
        """A video in the box (its frames from `offset` on), fitted to the box's width and height."""
        start, length, channel, name = self._span(start, length)
        w, h = w if w is not None else self.w, h if h is not None else self.h
        strip = self.editor.strips.new_movie(name, path, channel, start - offset, fit_method="ORIGINAL")
        if offset:
            strip.frame_offset_start = offset
        strip.frame_final_duration = min(length, strip.frame_final_duration)
        mw = strip.elements[0].orig_width if strip.elements else w
        mh = strip.elements[0].orig_height if strip.elements else h
        scale = min(w / mw, h / mh)
        strip.transform.scale_x = strip.transform.scale_y = scale
        self._place(strip, x + (w - mw * scale) / 2, y + (h - mh * scale) / 2, mw * scale, mh * scale, start,
                    length, fade)
        return self

    def text(self, words, x, y, size, hex_color, font_=None, anchor=("LEFT", "TOP"), bold=False, width=None,
             start=None, length=None, fade=0, box=None, box_margin=0.6, align="LEFT", line_spacing=1.15):
        """Text with its anchor point at pixel (x, y); `width` wraps it; `box` puts a colour behind it."""
        start, length, channel, name = self._span(start, length)
        strip = self.editor.strips.new_effect(name, "TEXT", channel, start, length=length)
        strip.text = words
        strip.font = font_ or font()
        strip.font_size = size
        strip.color = srgb(hex_color)
        strip.use_bold = bold
        strip.location = (x / self.w, 1 - y / self.h)
        strip.anchor_x, strip.anchor_y = anchor
        strip.alignment_x = align
        if hasattr(strip, "space_line"):
            strip.space_line = line_spacing
        if width:
            strip.wrap_width = width / self.w
        if box:
            strip.use_box = True
            strip.box_color = srgb(box)
            strip.box_margin = box_margin * size / self.w if box_margin < 1 else box_margin / self.w
            if hasattr(strip, "box_roundness"):
                strip.box_roundness = 1.0
        self._fade(strip, start, length, fade)
        return self

    def render_png(self, path, frame=1):
        scene = self.scene
        scene.frame_set(frame)
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.filepath = path
        _render_scene(scene, still=True)
        return path

    def render_mp4(self, path, stage="layout"):
        scene = self.scene
        _mp4(scene, path)
        _render_scene(scene, still=False, stage=stage)
        return _finished_mp4(path)


def _render_scene(scene, still, stage="layout"):
    window_scene = bpy.context.window.scene if bpy.context.window else None
    if bpy.context.window:
        bpy.context.window.scene = scene
    try:
        with bpy.context.temp_override(scene=scene):
            if still:
                bpy.ops.render.render(write_still=True, scene=scene.name)
            else:
                _render_animation_named(scene, stage)
    finally:
        if bpy.context.window and window_scene:
            bpy.context.window.scene = window_scene


def _render_animation_named(scene, stage):
    total = len(range(scene.frame_start, scene.frame_end + 1))
    done = [0]

    def after(*_):
        done[0] += 1
        if done[0] % 6 == 0 or done[0] == total:
            bbstate.progress(done[0], total, stage)
    bpy.app.handlers.render_post.append(after)
    try:
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        bpy.app.handlers.render_post.remove(after)


# Logo ------------------------------------------------------------------------------------------------------------------

def make_logo(brand, path, height=400, on_dark=True):
    """The brand's logo as a transparent PNG: an e-paper slab with tap waves in the accent colour, and the name."""
    scene = bpy.data.scenes.new("Logo")
    coll = scene.collection

    def emit(name, hex_color):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        em = nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = linear(hex_color)
        mat.node_tree.links.new(em.outputs[0], out.inputs[0])
        return mat

    ink = brand.paper if on_dark else brand.ink
    mat_ink, mat_accent = emit("Logo ink", ink), emit("Logo accent", brand.accent)

    def curve(name, points, closed=False, bevel=0.0, mat=None, fill=False):
        data = bpy.data.curves.new(name, "CURVE")
        data.dimensions = "2D"
        data.fill_mode = "BOTH" if fill else "NONE"
        spline = data.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for p, (x, y) in zip(spline.points, points):
            p.co = (x, y, 0, 1)
        spline.use_cyclic_u = closed
        data.bevel_depth = bevel
        data.bevel_resolution = 4
        obj = bpy.data.objects.new(name, data)
        obj.data.materials.append(mat)
        coll.objects.link(obj)
        return obj

    def rounded_rect(w, h, r, steps=10):
        pts = []
        for cx, cy, a0 in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180),
                           (w / 2 - r, -h / 2 + r, 270)):
            for i in range(steps + 1):
                a = math.radians(a0 + 90 * i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    # The mark: a portrait slab (the e-paper stand) with three tap waves coming off its corner.
    curve("Slab", rounded_rect(0.62, 0.9, 0.1), closed=True, bevel=0.035, mat=mat_ink)
    curve("Slab line 1", [(-0.17, 0.2), (0.17, 0.2)], bevel=0.03, mat=mat_ink)
    curve("Slab line 2", [(-0.17, 0.02), (0.07, 0.02)], bevel=0.03, mat=mat_ink)
    for i, radius in enumerate((0.2, 0.33, 0.46)):
        pts = [(0.31 + radius * math.cos(math.radians(a)), -0.45 + radius * math.sin(math.radians(a)))
               for a in range(15, 76, 5)]
        curve(f"Wave {i}", pts, bevel=0.04, mat=mat_accent)
    text_data = bpy.data.curves.new("Wordmark", "FONT")
    text_data.body = brand.name
    text_data.font = brand.font
    text_data.size = 0.95
    text_data.offset = 0.012       # a touch bolder
    text_data.align_y = "CENTER"
    text = bpy.data.objects.new("Wordmark", text_data)
    text.location = (0.78, -0.02, 0)
    text.data.materials.append(mat_ink)
    coll.objects.link(text)

    layer = scene.view_layers[0]
    layer.update()
    cam = bpy.data.objects.new("Logo camera", bpy.data.cameras.new("Logo camera"))
    coll.objects.link(cam)
    cam.data.type = "ORTHO"
    scene.camera = cam
    depsgraph = layer.depsgraph
    pts = []
    for obj in coll.objects:
        if obj.type in ("CURVE", "FONT"):
            ev = obj.evaluated_get(depsgraph)
            mesh = ev.to_mesh()
            pts += [obj.matrix_world @ v.co for v in mesh.vertices]
            ev.to_mesh_clear()
    lo, hi = bounds(pts)
    pad = 0.1
    w, h = hi.x - lo.x + 2 * pad, hi.y - lo.y + 2 * pad
    cam.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, 10)
    cam.data.ortho_scale = max(w, h)
    scene.render.resolution_y = height
    scene.render.resolution_x = int(round(height * w / h))
    scene.render.resolution_percentage = 100
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return path


# Materials -------------------------------------------------------------------------------------------------------------

def apply_looks(instances, looks):
    """Dress a copy's objects: `looks` is [[pattern, {color, roughness, metallic, coat, emission, transmission}], ...],
    first match wins (patterns match instance paths, * wildcards). Objects no pattern matches keep the design's
    colours, made a little nicer (satin, not plastic)."""
    import fnmatch
    made = {}

    def material(spec):
        key = json.dumps(spec, sort_keys=True)
        if key in made:
            return made[key]
        mat = bpy.data.materials.new(spec.get("name", "Look"))
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = linear(spec.get("color", "#9A9A9A"))
        bsdf.inputs["Roughness"].default_value = spec.get("roughness", 0.45)
        bsdf.inputs["Metallic"].default_value = spec.get("metallic", 0.0)
        bsdf.inputs["Coat Weight"].default_value = spec.get("coat", 0.0)
        if spec.get("transmission"):
            bsdf.inputs["Transmission Weight"].default_value = spec["transmission"]
        if spec.get("emission"):
            bsdf.inputs["Emission Color"].default_value = linear(spec["emission"])
            bsdf.inputs["Emission Strength"].default_value = spec.get("emission_strength", 2.0)
        made[key] = mat
        return mat

    for path, obj in instances.items():
        spec = None
        for pattern, s in looks or []:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.split("__")[0], pattern) or \
                    fnmatch.fnmatch(path.split("/")[-1], pattern):
                spec = s
                break
        if spec is None:
            for slot in obj.material_slots:
                if slot.material and slot.material.node_tree:
                    bsdf = slot.material.node_tree.nodes.get("Principled BSDF")
                    if bsdf:
                        bsdf.inputs["Roughness"].default_value = 0.42
            continue
        if not obj.material_slots:
            obj.data.materials.append(None)
        for slot in obj.material_slots:
            slot.link = "OBJECT"
            slot.material = material(spec)
    return made


# The kit ---------------------------------------------------------------------------------------------------------------

QUALITY = {"final": {"scale": 1.0, "still": 48, "video": 16},
           "draft": {"scale": 0.5, "still": 12, "video": 6},
           "preview": {"scale": 0.25, "still": 4, "video": 2}}
ASSETS = ["logo", "cutouts", "hero", "social", "exploded", "reel", "promo"]


def clear_scene(scene=None):
    """Empty the 3D scene for the next shot: every object and collection in it, its world and its keys."""
    scene = scene or bpy.context.scene
    for coll in list(scene.collection.children_recursive):
        for obj in list(coll.objects):
            bpy.data.objects.remove(obj)
        bpy.data.collections.remove(coll)
    for obj in list(scene.collection.objects):
        bpy.data.objects.remove(obj)
    scene.camera = None
    if scene.animation_data:
        scene.animation_data_clear()


def _humanize(path):
    leaf = path.split("/")[-1].split("__")[0]
    leaf = leaf.rstrip("_0123456789") if any(c.isalpha() for c in leaf) else leaf
    return leaf.replace("_", " ").strip().capitalize()


def _keep(objects):
    return [o for o in objects if o.type == "MESH"]


class Kit:
    """Makes the marketing kit's assets one by one (each also usable alone from a script)."""

    def __init__(self, params, links):
        self.p = params
        self.brand = Brand(params.get("brand"))
        self.q = QUALITY.get(params.get("quality", "final"), QUALITY["final"])
        self.links = links
        self.product = getattr(links, params.get("product_alias", "product"))
        self.demo = getattr(links, params.get("demo_alias", "demo"), None)
        self.fps = 24
        self.logos = {}
        self.timings = {}

    def px(self, n):
        return max(2, int(round(n * self.q["scale"])))

    def path(self, name):
        return bbstate.scratch(name)

    # Pieces ------------------------------------------------------------------------------------------------------------

    def product_scene(self, name="Product"):
        """The 3D scene, emptied, with a dressed copy of the product."""
        scene = bpy.context.scene
        clear_scene(scene)
        copy = self.product(name=name)
        parts = self.product.instances(copy)
        apply_looks(parts, self.p.get("looks"))
        return scene, copy, parts

    def logo(self):
        if not self.logos:
            t = time.time()
            self.logos["dark"] = make_logo(self.brand, self.path("logo_on_dark.png"), height=self.px(400))
            self.logos["light"] = make_logo(self.brand, self.path("logo_on_light.png"), height=self.px(400),
                                            on_dark=False)
            self.timings["logo"] = round(time.time() - t, 1)
        return self.logos

    def stills(self, which):
        """Product renders (transparent, with their shadows): three-quarter, front, and the exploded one."""
        t = time.time()
        b = self.brand
        out = {}
        scene, copy, parts = self.product_scene("Stills")
        studio = Studio(parts.values(), b, look="dark", scene=scene)
        view = self.p.get("product_view") or (0.75, -1.0, 0.5)
        size = self.px(2000)
        if "cutouts" in which or "hero" in which or "social" in which:
            bbstate.progress(0, 1, "product stills")
            studio.frame(size, size, direction=view, margin=1.04)
            out["three_quarter"] = studio.render_png(self.path("cutout_three_quarter.png"), size, size,
                                                     self.q["still"], transparent=True)
            studio.frame(size, size, direction=(0.12, -1.0, 0.28), margin=1.04)
            out["front"] = studio.render_png(self.path("cutout_front.png"), size, size, self.q["still"],
                                             transparent=True)
        if "exploded" in which:
            bbstate.progress(0, 1, "exploded still")
            view_spec = self.product.exploded(copy, self.p.get("explode"))
            for obj, delta in view_spec.offsets().items():
                moved = obj.matrix_world.copy()
                moved.translation += view_spec.root.matrix_world.to_3x3() @ delta
                obj.matrix_world = moved
            bpy.context.view_layer.update()
            scene.view_layers[0].update()
            points = world_points(parts.values())
            w, h = self.px(1920), self.px(1080)
            studio.points = points
            studio.frame(w, h, direction=self.p.get("exploded_view") or (0.8, -1.0, 0.75),
                         window=(0.2, 0.08, 0.8, 0.95), margin=1.03, points=points)
            out["exploded"] = studio.render_png(self.path("exploded_render.png"), w, h, self.q["still"],
                                                transparent=True)
            out["callouts"] = self._callouts(studio, parts, view_spec, w, h)
        self.timings["stills"] = round(time.time() - t, 1)
        return out

    def _callouts(self, studio, parts, view_spec, w, h):
        """[(label, x, y)] in the exploded render's pixels: the design's named parts, or each step's biggest part."""
        wanted = self.p.get("callouts")
        picks = []
        if wanted:
            import fnmatch
            for pattern, label in (wanted.items() if isinstance(wanted, dict) else wanted):
                objs = [o for k, o in parts.items() if fnmatch.fnmatch(k, pattern) or
                        fnmatch.fnmatch(k.split("__")[0], pattern) or k.startswith(pattern + "__")]
                if objs:
                    picks.append((label, objs))
        else:
            for step in view_spec.steps:
                best, size = None, -1
                for pattern in step.get("moves", {}):
                    from steve_explode import _match
                    objs = _match(parts, pattern)
                    if objs:
                        lo, hi = bounds(world_points(objs))
                        if (hi - lo).length > size:
                            best, size = (pattern, objs), (hi - lo).length
                if best:
                    picks.append((_humanize(best[0]), best[1]))
        out = []
        scene = studio.scene
        scene.render.resolution_x, scene.render.resolution_y = w, h
        bpy.context.view_layer.update()
        for label, objs in picks:
            lo, hi = bounds(world_points(objs))
            cx, cy = studio.project((lo + hi) / 2, w, h)
            # Point at the part itself: its surface point nearest the middle of its outline in the picture (the
            # middle of a frame-shaped part is a hole).
            best, best_d = (cx, cy), None
            for obj in mesh_objects(objs):
                verts = obj.data.vertices
                step = max(1, len(verts) // 1500)
                mw = obj.matrix_world
                for i in range(0, len(verts), step):
                    co = world_to_camera_view(scene, studio.camera, mw @ verts[i].co)
                    px, py = co.x * w, (1 - co.y) * h
                    d = (px - cx) ** 2 + (py - cy) ** 2
                    if best_d is None or d < best_d:
                        best, best_d = (px, py), d
            out.append((label, best[0], best[1]))
        return out

    # Layouts -----------------------------------------------------------------------------------------------------------

    def hero(self, stills):
        b, s = self.brand, self.q["scale"]
        w, h = self.px(1920), self.px(1080)
        L = Layout(w, h, name="Hero")
        L.color(b.ink)
        L.image(stills["three_quarter"], int(w * 0.49), int(h * 0.07), int(w * 0.47), int(h * 0.86))
        L.image(self.logo()["dark"], self.px(110), self.px(90), self.px(330), self.px(96), align=(0, 0.5))
        L.text(balance(b["headline"], 22), self.px(110), self.px(560), self.px(84), b.paper, bold=True,
               width=self.px(860),
               anchor=("LEFT", "BOTTOM"), line_spacing=1.05)
        L.color(b.accent, self.px(110), self.px(600), self.px(120), self.px(8))
        L.text(b["subhead"], self.px(110), self.px(650), self.px(34), "#BFC3CA", width=self.px(760))
        L.text(b["cta"], self.px(130), self.px(905), self.px(32), b.ink, bold=True, box=b.accent,
               anchor=("LEFT", "CENTER"), box_margin=self.px(22))
        return bbstate.output(L.render_png(self.path("hero_16x9.png")))

    def social(self, stills):
        b = self.brand
        n = self.px(1080)
        L = Layout(n, n, name="Social")
        L.color(b.paper)
        L.color(b.accent, 0, n - self.px(18), n, self.px(18))
        L.text(balance(b["headline"], 26), n // 2, self.px(80), self.px(64), b.ink, bold=True, width=self.px(980),
               anchor=("CENTER", "TOP"), align="CENTER", line_spacing=1.05)
        L.image(stills["three_quarter"], self.px(90), self.px(250), n - self.px(180), n - self.px(380))
        L.image(self.logo()["light"], self.px(70), n - self.px(120), self.px(240), self.px(70), align=(0, 0.5))
        L.text(b["tagline"], n - self.px(70), n - self.px(85), self.px(30), b.slate, anchor=("RIGHT", "CENTER"))
        return bbstate.output(L.render_png(self.path("social_square.png")))

    def cutouts(self, stills):
        return [bbstate.output(stills["three_quarter"]), bbstate.output(stills["front"])]

    def exploded(self, stills):
        b = self.brand
        w, h = self.px(1920), self.px(1080)
        L = Layout(w, h, name="Exploded")
        L.color(b.ink)
        L.image(stills["exploded"], 0, 0, w, h)
        L.text(self.p.get("exploded_title") or f"Inside the {b.name} stand", self.px(80), self.px(70),
               self.px(46), b.paper, bold=True)
        L.color(b.accent, self.px(80), self.px(140), self.px(90), self.px(6))
        # Labels in two columns, each near its part's height, pushed apart so they never overlap.
        left = sorted([c for c in stills["callouts"] if c[1] < w * 0.5], key=lambda c: c[2])
        right = sorted([c for c in stills["callouts"] if c[1] >= w * 0.5], key=lambda c: c[2])
        gap = self.px(64)
        for column, side in ((left, "LEFT"), (right, "RIGHT")):
            y_prev = self.px(190)
            for label, x, y in column:
                ly = max(y, y_prev + gap)
                ly = min(ly, h - self.px(60))
                y_prev = ly
                lx = self.px(80) if side == "LEFT" else w - self.px(80)
                end = lx + self.px(300) if side == "LEFT" else lx - self.px(300)
                L.line(end, ly, x, y, b.accent, thickness=max(2, self.px(3)))
                L.color(b.accent, x - self.px(7), y - self.px(7), self.px(14), self.px(14))
                L.line(lx, ly, end, ly, b.accent, thickness=max(2, self.px(3)))
                L.text(label, lx, ly - self.px(12), self.px(28), b.paper, anchor=(side, "BOTTOM"), bold=True)
        L.image(self.logo()["dark"], w - self.px(300), h - self.px(110), self.px(220), self.px(64), align=(1, 0.5))
        return bbstate.output(L.render_png(self.path("exploded_labels.png")))

    # Videos ------------------------------------------------------------------------------------------------------------

    def _demo_scene(self, name):
        """A scene with the in-use design playing its animation; returns (scene, studio-ready parts, first, last)."""
        scene = bpy.context.scene
        clear_scene(scene)
        copy = self.demo(name=name)
        parts = self.demo.instances(copy)
        apply_looks(parts, self.p.get("looks"))
        last = self.demo.play(copy, self.p.get("demo_animation"), start_frame=1, set_range=False)
        for spec in self.p.get("highlights") or []:
            self._highlight(parts, spec)
        return scene, copy, parts, 1, last

    def _highlight(self, parts, spec):
        """Make matching parts glow over time: {"parts": pattern, "color": "#RRGGBB", "keys": [[seconds, strength]]}."""
        import fnmatch
        mat = bpy.data.materials.new("Highlight")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        base = spec.get("base") or {}
        bsdf.inputs["Base Color"].default_value = linear(base.get("color", "#D8D6D0"))
        bsdf.inputs["Roughness"].default_value = base.get("roughness", 0.15)
        bsdf.inputs["Coat Weight"].default_value = base.get("coat", 0.6)
        bsdf.inputs["Emission Color"].default_value = linear(spec.get("color", self.brand.accent))
        socket = bsdf.inputs["Emission Strength"]
        for seconds, strength in spec.get("keys", []):
            socket.default_value = strength
            socket.keyframe_insert("default_value", frame=1 + round(seconds * self.fps))
        for key, obj in parts.items():
            if fnmatch.fnmatch(key, spec["parts"]) or fnmatch.fnmatch(key.split("/")[-1], spec["parts"]):
                for slot in obj.material_slots:
                    slot.link = "OBJECT"
                    slot.material = mat

    def reel(self):
        if self.demo is None:
            bbstate.WARNINGS.append("reel: no demo design (design_2) with an animation, so no reel.")
            return None
        b = self.brand
        w, h = self.px(1080), self.px(1920)
        scene, copy, parts, first, last = self._demo_scene("Reel")
        studio = Studio(parts.values(), b, look="dark", scene=scene,
                        points=world_points(parts.values(), range(first, last + 1, 6)))
        studio.frame(w, h, direction=self.p.get("demo_view") or (1.0, -0.6, 0.45), window=(0.04, 0.2, 0.96, 0.78),
                     margin=1.04)
        clip = studio.render_mp4(self.path("reel_clip.mp4"), w, h, first, last, self.q["video"], stage="reel")
        n = last - first + 1
        card = self.fps * 2
        L = Layout(w, h, frames=n + card, name="Reel layout")
        L.color(b.ink)
        L.movie(clip, 0, 0, w, h, start=1, length=n)
        lines = b["reel_captions"] or []
        beat = n // max(len(lines), 1)
        for i, line in enumerate(lines):
            L.text(line, w // 2, self.px(240), self.px(120), b.paper if i < len(lines) - 1 else b.accent,
                   bold=True, anchor=("CENTER", "CENTER"), align="CENTER", start=1 + i * beat, length=beat,
                   fade=(4, 4))
        L.color(b.ink, start=n - 8, length=card + 8, fade=(10, 0))
        L.image(self.logo()["dark"], self.px(170), int(h * 0.38), w - self.px(340), self.px(200), start=n,
                length=card, fade=(8, 0))
        L.text(b["tagline"], w // 2, int(h * 0.56), self.px(56), b.paper, anchor=("CENTER", "CENTER"),
               align="CENTER", start=n + 6, length=card - 6, fade=(8, 0))
        L.text(b["url"], w // 2, int(h * 0.62), self.px(40), b.accent, anchor=("CENTER", "CENTER"),
               align="CENTER", start=n + 10, length=card - 10, fade=(8, 0))
        return bbstate.output(L.render_mp4(self.path("reel_9x16.mp4"), stage="reel layout"))

    def promo(self):
        b = self.brand
        w, h = self.px(1920), self.px(1080)
        fps, xf = self.fps, 10
        segments = []

        # 1. A slow orbit around the assembled product.
        scene, copy, parts = self.product_scene("Promo orbit")
        studio = Studio(parts.values(), b, look="dark", scene=scene)
        n = fps * 3
        base = Vector(self.p.get("product_view") or (0.75, -1.0, 0.5))
        for i in range(7):
            a = math.radians(-35 + 50 * i / 6)
            d = Vector((base.x * math.cos(a) - base.y * math.sin(a), base.x * math.sin(a) + base.y * math.cos(a),
                        base.z))
            studio.frame(w, h, direction=d, window=(0.1, 0.06, 0.9, 0.84), frame=1 + round(i * (n - 1) / 6))
        segments.append((studio.render_mp4(self.path("promo_orbit.mp4"), w, h, 1, n, self.q["video"],
                                           stage="promo 1/3 orbit"), n, (b["captions"] or [""])[0]))

        # 2. The product in use (the demo design's animation).
        if self.demo is not None:
            scene, copy, parts, first, last = self._demo_scene("Promo demo")
            studio = Studio(parts.values(), b, look="dark", scene=scene,
                            points=world_points(parts.values(), range(first, last + 1, 6)))
            studio.frame(w, h, direction=self.p.get("demo_view") or (1.0, -0.6, 0.45), window=(0.1, 0.06, 0.9, 0.84))
            segments.append((studio.render_mp4(self.path("promo_demo.mp4"), w, h, first, last, self.q["video"],
                                               stage="promo 2/3 in use"), last - first + 1,
                             (b["captions"] or ["", ""])[1 % max(len(b["captions"] or [""]), 1)]))

        # 3. The exploded view.
        scene, copy, parts = self.product_scene("Promo explode")
        timeline = self.product.explode(copy, self.p.get("explode"), start_frame=8, hold=0.8, speed=2.0,
                                        set_range=False)
        last = timeline["end"]
        studio = Studio(parts.values(), b, look="dark", scene=scene,
                        points=world_points(parts.values(), [1, timeline["exploded"]]),
                        floor_z=bounds(world_points(parts.values(), [1]))[0].z)
        studio.frame(w, h, direction=(0.85, -1.0, 0.7), window=(0.08, 0.04, 0.92, 0.86), frame=1)
        studio.frame(w, h, direction=(1.0, -0.8, 0.75), window=(0.08, 0.04, 0.92, 0.86), frame=last)
        segments.append((studio.render_mp4(self.path("promo_explode.mp4"), w, h, 1, last, self.q["video"],
                                           stage="promo 3/3 exploded"), last,
                         (b["captions"] or ["", "", ""])[-1]))

        card = fps * 3
        total = sum(n for _, n, _ in segments) - xf * (len(segments) - 1) + card - xf
        L = Layout(w, h, frames=total, name="Promo layout")
        L.color(b.ink)
        at = 1
        for i, (clip, n, caption) in enumerate(segments):
            L.movie(clip, 0, 0, w, h, start=at, length=n, fade=(xf if i else 0, 0))
            if caption:
                L.color(b.accent, self.px(100), h - self.px(150), self.px(10), self.px(70), start=at + 8,
                        length=n - 20, fade=(6, 6))
                L.text(caption, self.px(135), h - self.px(115), self.px(54), b.paper, bold=True,
                       anchor=("LEFT", "CENTER"), start=at + 8, length=n - 20, fade=(6, 6))
            at += n - xf
        L.color(b.ink, start=at, length=card + xf, fade=(xf, 0))
        L.image(self.logo()["dark"], int(w * 0.3), int(h * 0.3), int(w * 0.4), self.px(170), start=at + 4,
                length=card + xf - 4, fade=(10, 0))
        L.text(b["tagline"], w // 2, int(h * 0.58), self.px(54), b.paper, anchor=("CENTER", "CENTER"),
               align="CENTER", start=at + 10, length=card + xf - 10, fade=(10, 0))
        L.text(b["cta"], w // 2, int(h * 0.7), self.px(36), b.ink, bold=True, box=b.accent,
               anchor=("CENTER", "CENTER"), align="CENTER", start=at + 16, length=card + xf - 16, fade=(10, 0),
               box_margin=self.px(20))
        return bbstate.output(L.render_mp4(self.path("promo_16x9.mp4"), stage="promo layout"))


def make_kit(params, links=None):
    """The whole kit (or params["assets"] of it): see ASSETS. Each finished file goes to STEVE as an output."""
    import sys
    links = links or sys.modules["links"]
    kit = Kit(params, links)
    which = params.get("assets") or ASSETS
    if isinstance(which, str):
        which = [a.strip() for a in which.split(",") if a.strip()]
    unknown = [a for a in which if a not in ASSETS]
    if unknown:
        raise ValueError(f"Unknown assets {unknown}: choose from {ASSETS}")
    made = {}

    def step(name, fn, *args):
        t = time.time()
        result = fn(*args)
        kit.timings[name] = round(time.time() - t, 1)
        made[name] = result
        return result

    if "logo" in which:
        logos = kit.logo()
        made["logo"] = [bbstate.output(logos["dark"]), bbstate.output(logos["light"])]
    stills = kit.stills(which) if {"cutouts", "hero", "social", "exploded"} & set(which) else {}
    for name in ("cutouts", "hero", "social", "exploded"):
        if name in which:
            step(name, getattr(kit, name), stills)
    for name in ("reel", "promo"):
        if name in which:
            step(name, getattr(kit, name))
    bbstate.DETAILS["kit"] = {"brand": kit.brand.spec, "quality": params.get("quality", "final"),
                              "assets": {k: (v if isinstance(v, list) else [v]) for k, v in made.items() if v},
                              "timings_s": kit.timings}
    return made
