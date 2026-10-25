"""Runs one RenderBuddy job inside Blender, the way STEVE's built-in Blender documents run.

    blender -b --factory-startup --python-exit-code 1 -P blender_runner.py -- job.json

job.json: {"script": "main.py path", "links": {"alias": "design.3mf path"}, "out": "output folder",
           "mode": "auto" | "still" | "video" | "blend", "device": "gpu" | "cpu"}

The scene starts empty with STEVE's defaults (Cycles, 16 samples denoised, 1280 x 720, 24 fps, frames 1-250, AgX).
`from links import <alias>` gives the same callables as STEVE: alias(at=, rotate=, name=) adds a copy and returns its
collection; alias.root(copy) is the empty it hangs from; alias.instances(copy) maps body names to objects.
Writes still.png, animation.mp4 (animated scenes with a camera), scene.blend and report.json to the output folder.
Progress lines on stdout: "BB_PROGRESS <done> <total> <stage>".
"""
import json
import math
import os
import sys
import time
import traceback
import types

import bpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from threemf import read_3mf  # noqa: E402
from steve_motion import Rig, fcurves  # noqa: E402
from steve_explode import Explode, auto_spec  # noqa: E402
import bbstate  # noqa: E402


NOTES = {"warnings": [], "played": []}    # what the script's motion calls did, for report.json


def progress(done, total, stage):
    print(f"BB_PROGRESS {done} {total} {stage}", flush=True)


# Links -----------------------------------------------------------------------------------------------------------------

def steve_name(raw):
    """A 3MF body name ("stand / epaper panel · glass") as STEVE's instance path and object name:
    ("stand/epaper_panel__glass", "epaper_panel__glass"). A body of a multi-body part gets "__<body>"."""
    path, _, body = raw.partition(" · ")
    parts = [p.strip().replace(" ", "_") for p in path.split(" / ") if p.strip()]
    suffix = "__" + body.strip().replace(" ", "_") if body.strip() else ""
    return "/".join(parts) + suffix, (parts[-1] if parts else "body") + suffix


class Link:
    """One linked STEVE design, read from its 3MF package; calling it adds an independent copy to the scene."""

    def __init__(self, alias, path, kinematics=None):
        self.alias, self.path = alias, path
        self.kinematics = kinematics or {}
        self._data = None
        self._meshes = {}
        self._copies = {}

    def _load(self):
        if self._data is not None:
            return
        self._data = read_3mf(self.path)
        materials = {}
        for oid, obj in self._data["objects"].items():
            if obj.vertices is None or not len(obj.vertices):
                continue
            mesh = bpy.data.meshes.new(f"{self.alias}_{obj.name}")
            mesh.from_pydata(obj.vertices.tolist(), [], obj.triangles.tolist())
            if obj.colors is not None:
                index = []
                slots = {}
                for rgba in obj.colors:
                    key = tuple(round(c, 4) for c in rgba)
                    if key not in materials:
                        mat = bpy.data.materials.new(f"mat_{len(materials)}")
                        mat.use_nodes = True
                        bsdf = mat.node_tree.nodes.get("Principled BSDF")
                        if bsdf:
                            bsdf.inputs["Base Color"].default_value = key
                            bsdf.inputs["Roughness"].default_value = 0.5
                        mat.diffuse_color = key
                        materials[key] = mat
                    if key not in slots:
                        slots[key] = len(slots)
                        mesh.materials.append(materials[key])
                    index.append(slots[key])
                mesh.polygons.foreach_set("material_index", index)
            mesh.validate()
            mesh.update()
            for poly in mesh.polygons:
                poly.use_smooth = False
            self._meshes[oid] = mesh

    def __call__(self, at=(0, 0, 0), rotate=None, name=None):
        self._load()
        scene = bpy.context.scene
        coll = bpy.data.collections.new(name or self.alias)
        scene.collection.children.link(coll)
        root = bpy.data.objects.new(f"{coll.name} root", None)
        coll.objects.link(root)
        turn = Matrix.Identity(4)
        if rotate:
            for axis, degrees in ([rotate] if isinstance(rotate[0], str) else rotate):
                turn = Matrix.Rotation(math.radians(degrees), 4, axis.upper()) @ turn
        root.matrix_basis = Matrix.Translation(at) @ turn
        instances = {}
        for item in self._data["items"]:
            mesh = self._meshes.get(item["object"])
            if mesh is None:
                continue
            path, leaf = steve_name(item["name"])
            obj = bpy.data.objects.new(leaf, mesh)
            coll.objects.link(obj)
            obj.parent = root
            obj.matrix_basis = Matrix(item["matrix"].tolist())
            key = path
            n = 2
            while key in instances:
                key, n = f"{path}_{n}", n + 1
            instances[key] = obj
        self._copies[coll.name] = {"root": root, "instances": instances}
        bpy.context.view_layer.update()
        return coll

    def root(self, copy):
        return self._copies[copy.name]["root"]

    def instances(self, copy):
        return dict(self._copies[copy.name]["instances"])

    def rig(self, copy):
        """The copy's joints, made on first use from the design's kinematics (see steve_motion.Rig)."""
        entry = self._copies[copy.name]
        if "rig" not in entry:
            entry["rig"] = Rig(self.kinematics, entry["root"], entry["instances"])
            if entry["rig"].missing:
                NOTES["warnings"].append(f"{self.alias} joints: no bodies for {', '.join(entry['rig'].missing)}")
        return entry["rig"]

    def pose(self, copy, values, frame=None):
        """Pose a copy: a pose name from the design, or {joint or coupling: value}; keyed at `frame` if given."""
        self.rig(copy).pose(values, frame)

    def play(self, copy, name=None, start_frame=None, set_range=True, seconds=None):
        """Key one of the design's animations (or drives) onto the timeline; returns its last frame."""
        return self.rig(copy).play(name, start_frame=start_frame, set_range=set_range, seconds=seconds)

    def explodes(self):
        """The names of the design's exploded views."""
        return [e.get("name") for e in self.kinematics.get("explodes", [])]

    def exploded(self, copy, name=None, auto=False, gap=None):
        """One of the design's exploded views on a copy (the first by default), or with auto=True (or when the
        design has none) a teardown worked out from the geometry. Its .play(...) keys it; .bounds() is the
        exploded extent to frame the camera on; .steps lists the steps."""
        entry = self._copies[copy.name]
        specs = {e.get("name"): e for e in self.kinematics.get("explodes", [])}
        if auto or not specs:
            spec = auto_spec(entry["root"], entry["instances"], gap=gap)
        elif name is None:
            spec = next(iter(specs.values()))
        elif name in specs:
            spec = specs[name]
        else:
            raise KeyError(f"No exploded view named {name!r}: {list(specs)}")
        return Explode(entry["root"], entry["instances"], spec)

    def explode(self, copy, name=None, start_frame=None, set_range=True, hold=1.0, reassemble=False, speed=1.0,
                auto=False, gap=None):
        """Key an exploded view onto the timeline: the design's `name` (default its first), or auto=True for one
        worked out from the geometry. Holds `hold` seconds exploded, then with reassemble plays it backwards.
        speed=2 plays it twice as fast. Returns {"start", "end", "exploded", "steps": [{name, start_frame,
        end_frame}], "missing"} (frames); with set_range the scene's frame range becomes start to end."""
        view = self.exploded(copy, name, auto=auto, gap=gap)
        timeline = view.play(start_frame=start_frame, set_range=set_range, hold=hold, reassemble=reassemble,
                             speed=speed)
        if view.missing:
            NOTES["warnings"].append(f"{self.alias} explode {view.name!r}: no bodies for {', '.join(view.missing)}")
        NOTES["played"].append({"alias": self.alias, "explode": view.name, "auto": bool(view.spec.get("auto")),
                                "frames": [timeline["start"], timeline["end"]],
                                "steps": [s["name"] for s in timeline["steps"]]})
        return timeline


# Scene setup -----------------------------------------------------------------------------------------------------------

def action_fcurves_shim():
    """Blender 5 removed Action.fcurves, which STEVE's Blender 4.5 recipes use: give it back, read-only."""
    if hasattr(bpy.types.Action, "fcurves"):
        return

    def get(action):
        from bpy_extras import anim_utils
        for slot in action.slots:
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            if bag:
                return bag.fcurves
        return []
    bpy.types.Action.fcurves = property(get)


def steve_defaults():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    action_fcurves_shim()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.render.resolution_percentage = 100
    scene.render.fps, scene.render.fps_base = 24, 1
    scene.frame_start, scene.frame_end = 1, 250
    scene.view_settings.view_transform = "AgX"


def use_device(kind):
    """Cycles on the GPU (Metal on a Mac, else CUDA/OptiX/HIP/oneAPI) when asked and available; returns what was used."""
    if kind != "gpu":
        return "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            bpy.context.scene.cycles.device = "GPU"
            return f"{backend}: " + ", ".join(sorted({d.name for d in gpus}))
    return "CPU"


def is_animated():
    def animated(idb):
        ad = getattr(idb, "animation_data", None)
        return bool(ad and (ad.action or len(ad.drivers)))
    things = [bpy.context.scene, bpy.context.scene.world]
    for obj in bpy.data.objects:
        things += [obj, obj.data]
        if getattr(obj.data, "shape_keys", None):
            things.append(obj.data.shape_keys)
    for mat in bpy.data.materials:
        things += [mat, mat.node_tree]
    things += list(bpy.data.node_groups)
    if bpy.context.scene.world:
        things.append(bpy.context.scene.world.node_tree)
    return any(animated(t) for t in things if t is not None)


def set_video_output(scene, path):
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):          # Blender 5.0+
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.ffmpeg.audio_codec = "NONE"
    scene.render.filepath = path


def set_png_output(scene, path):
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGBA" if scene.render.film_transparent else "RGB"
    scene.render.filepath = path


# The job ---------------------------------------------------------------------------------------------------------------

def run(job):
    out = job["out"]
    os.makedirs(out, exist_ok=True)
    report = {"blender": bpy.app.version_string, "warnings": [], "outputs": {}, "timings_s": {}}
    t0 = time.time()

    steve_defaults()
    bbstate.OUT = out
    bbstate.DEVICE = use_device(job.get("device", "gpu"))
    links = types.ModuleType("links")
    for alias, path in (job.get("links") or {}).items():
        setattr(links, alias, Link(alias, path, (job.get("kinematics") or {}).get(alias)))
    sys.modules["links"] = links

    script = job["script"]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    with open(script, encoding="utf-8") as f:
        code = compile(f.read(), "main.py", "exec")
    progress(0, 1, "building scene")
    exec(code, {"__name__": "__main__", "__file__": script})
    bpy.context.view_layer.update()
    report["timings_s"]["scene"] = round(time.time() - t0, 2)

    scene = bpy.context.scene
    report["device"] = use_device(job.get("device", "gpu")) if scene.render.engine == "CYCLES" else scene.render.engine
    if bbstate.OUTPUTS:
        report["device"] = bbstate.DEVICE
    animated = is_animated()
    scale = scene.render.resolution_percentage / 100
    report["scene"] = {
        "engine": scene.render.engine,
        "resolution": [int(scene.render.resolution_x * scale), int(scene.render.resolution_y * scale)],
        "samples": scene.cycles.samples if scene.render.engine == "CYCLES" else None,
        "frames": [scene.frame_start, scene.frame_end], "frame_step": scene.frame_step,
        "fps": scene.render.fps / scene.render.fps_base, "animated": animated,
        "camera": scene.camera.name if scene.camera else None,
        "objects": len(bpy.data.objects), "lights": sum(o.type == "LIGHT" for o in bpy.data.objects),
        "mesh_names": sorted(o.name for o in bpy.data.objects if o.type == "MESH")[:120],
    }
    report["warnings"] += NOTES["warnings"] + bbstate.WARNINGS
    report.update(bbstate.DETAILS)
    if NOTES["played"]:
        report["motion"] = NOTES["played"]
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        report["warnings"].append("The scene has no lights: only the world lights it.")

    mode = job.get("mode", "auto")
    report["outputs"].update(bbstate.OUTPUTS)
    if mode == "outputs":
        # The script made and handed over its own files (bbkit.make_kit and friends).
        report["timings_s"]["total"] = round(time.time() - t0, 2)
        return report
    blend = os.path.join(out, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True, copy=True)
    report["outputs"]["blend"] = "scene.blend"
    if mode == "blend":
        return report
    if scene.camera is None:
        raise RuntimeError("The scene has no camera (set scene.camera): nothing to render.")

    if mode in ("auto", "still"):
        t = time.time()
        progress(0, 1, "rendering still")
        scene.frame_set(scene.frame_start)
        set_png_output(scene, os.path.join(out, "still.png"))
        bpy.ops.render.render(write_still=True)
        report["timings_s"]["still"] = round(time.time() - t, 2)
        report["outputs"]["still"] = "still.png"

    wants_video = mode == "video" or (mode == "auto" and animated)
    if wants_video:
        if scene.frame_end <= scene.frame_start:
            report["warnings"].append("Only one frame: no video.")
        else:
            if not animated:
                report["warnings"].append("Nothing in the scene is animated: the video is a still.")
            total = len(range(scene.frame_start, scene.frame_end + 1, scene.frame_step))
            done = [0]

            def after_frame(*_):
                done[0] += 1
                progress(done[0], total, "rendering video")
            bpy.app.handlers.render_post.append(after_frame)
            t = time.time()
            set_video_output(scene, os.path.join(out, "animation.mp4"))
            bpy.ops.render.render(animation=True)
            bpy.app.handlers.render_post.remove(after_frame)
            elapsed = time.time() - t
            report["timings_s"]["video"] = round(elapsed, 2)
            report["timings_s"]["per_frame"] = round(elapsed / max(total, 1), 2)
            produced = [n for n in os.listdir(out) if n.startswith("animation") and n.endswith(".mp4")]
            if produced and produced[0] != "animation.mp4":
                os.replace(os.path.join(out, produced[0]), os.path.join(out, "animation.mp4"))
            report["outputs"]["animation"] = "animation.mp4"
            report["video"] = {"frames": total, "seconds": round(total * scene.frame_step / report["scene"]["fps"], 3)}
    report["timings_s"]["total"] = round(time.time() - t0, 2)
    return report


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    with open(argv[0], encoding="utf-8") as f:
        job = json.load(f)
    try:
        report = run(job)
        report["ok"] = True
    except Exception:
        report = {"ok": False, "error": traceback.format_exc(limit=8)}
    with open(os.path.join(job["out"], "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("BB_DONE", "ok" if report["ok"] else "failed", flush=True)
    if not report["ok"]:
        print(report["error"], file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
