"""STEVE exploded views, played in Blender.

A design's `design.explode(...)` comes in its kinematics (read_design(document_id, detail="kinematics")) as
"explodes": each step has a start and duration in seconds, a hold, and `moves`, {instance path: [dx, dy, dz]} in
millimetres in the design's frame, added to every earlier step's moves. play() keys those moves onto a copy's objects.
For a design without one, auto() works out a teardown from the copy's geometry, as STEVE's `auto=True` does.
"""
import fnmatch

import bpy
from mathutils import Vector

from steve_motion import fcurves, _objects_for


def _match(instances, pattern):
    """The objects an explode names: an instance path (and its bodies and children), or a pattern ("screw_*")."""
    if any(ch in pattern for ch in "*?["):
        names = [key for key in instances if fnmatch.fnmatch(key, pattern) or fnmatch.fnmatch(key.split("__")[0],
                                                                                                pattern)]
        return [instances[key] for key in names]
    return _objects_for(instances, pattern)


def _ease(objects, interpolation):
    for obj in objects:
        for curve in fcurves(obj):
            if curve.data_path != "location":
                continue
            for point in curve.keyframe_points:
                point.interpolation = interpolation
                point.easing = "AUTO"
                point.handle_left_type = point.handle_right_type = "AUTO_CLAMPED"


def _world_box(objects):
    lo, hi = Vector((1e18,) * 3), Vector((-1e18,) * 3)
    for obj in objects:
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


class Explode:
    """One exploded view on one copy: `steps` as STEVE lists them, with each object's rest location."""

    def __init__(self, root, instances, spec):
        self.root, self.instances, self.spec = root, instances, spec
        self.name = spec.get("name", "explode")
        self.steps = spec.get("steps", [])
        self.missing = sorted({p for s in self.steps for p in s.get("moves", {}) if not _match(instances, p)})

    def _local(self, obj, delta):
        """A design-frame move (mm) in obj's parent frame, the frame its location is keyed in."""
        bpy.context.view_layer.update()
        world = self.root.matrix_world.to_3x3() @ Vector(delta)
        parent = obj.parent.matrix_world.to_3x3() if obj.parent else None
        return parent.inverted() @ world if parent else world

    def offsets(self, upto=None):
        """{object: total design-frame move} after `upto` steps (all by default)."""
        total = {}
        for step in self.steps[:upto]:
            for pattern, delta in step.get("moves", {}).items():
                for obj in _match(self.instances, pattern):
                    total[obj] = total.get(obj, Vector()) + Vector(delta)
        return total

    def bounds(self, upto=None):
        """World (lo, hi) of every object of the copy with the explode applied: frame the camera on this."""
        lo, hi = _world_box(self.instances.values())
        for obj, delta in self.offsets(upto).items():
            move = self.root.matrix_world.to_3x3() @ delta
            olo, ohi = _world_box([obj])
            lo = Vector(map(min, lo, olo + move))
            hi = Vector(map(max, hi, ohi + move))
        return lo, hi

    def play(self, start_frame=None, fps=None, set_range=True, hold=1.0, reassemble=False, speed=1.0):
        """Key the explode from `start_frame`: each step over its duration, holding between steps; then hold `hold`
        seconds exploded and, with reassemble, play it backwards. Returns {"start", "end", "steps": [{name,
        start_frame, end_frame}]}; with set_range the scene's frame range becomes that."""
        scene = bpy.context.scene
        fps = fps or scene.render.fps / scene.render.fps_base
        start = scene.frame_start if start_frame is None else start_frame
        interpolation = "LINEAR" if self.spec.get("easing") == "linear" else "BEZIER"
        rest = {}
        current = {}
        touched = set()

        def frame_at(seconds):
            return start + round(seconds * fps / speed)

        def key(obj, frame, offset):
            if obj not in rest:
                rest[obj] = obj.location.copy()
            obj.location = rest[obj] + self._local(obj, offset)
            obj.keyframe_insert("location", frame=frame)
            touched.add(obj)

        timeline = []
        clock = 0.0
        for step in self.steps:
            begin = step.get("start", clock)
            length = step.get("duration", 1.0)
            moved = {}
            for pattern, delta in step.get("moves", {}).items():
                for obj in _match(self.instances, pattern):
                    moved[obj] = moved.get(obj, Vector()) + Vector(delta)
            f0, f1 = frame_at(begin), frame_at(begin + length)
            for obj, delta in moved.items():
                before = current.get(obj, Vector())
                key(obj, f0, before)
                current[obj] = before + delta
                key(obj, f1, current[obj])
            timeline.append({"name": step.get("name", f"step {len(timeline) + 1}"), "start_frame": f0,
                             "end_frame": f1})
            clock = begin + length + step.get("hold", 0.25)
        exploded_end = frame_at(clock - (self.steps[-1].get("hold", 0.25) if self.steps else 0)) if self.steps else start
        last = exploded_end + round(hold * fps)
        if reassemble and self.steps:
            span = exploded_end - start
            back = []
            for item in reversed(timeline):
                # Mirror each step about the exploded moment: the last part out is the first back in.
                b0 = last + (exploded_end - item["end_frame"])
                b1 = last + (exploded_end - item["start_frame"])
                back.append({"name": item["name"] + " (back)", "start_frame": b0, "end_frame": b1})
            for obj, frames in self._mirror_keys(touched, exploded_end, last).items():
                for frame, location in frames:
                    obj.location = location
                    obj.keyframe_insert("location", frame=frame)
            timeline += back
            last = last + span
        _ease(touched, interpolation)
        if set_range:
            scene.frame_start, scene.frame_end = start, max(last, start + 1)
        return {"start": start, "end": last, "exploded": exploded_end, "steps": timeline,
                "missing": self.missing}

    def _mirror_keys(self, objects, pivot, offset_to):
        """For each object, its keys up to `pivot` mirrored to run backwards from `offset_to`."""
        out = {}
        for obj in objects:
            keys = {}
            for curve in fcurves(obj):
                if curve.data_path != "location":
                    continue
                for point in curve.keyframe_points:
                    keys.setdefault(round(point.co.x), {})[curve.array_index] = point.co.y
            frames = []
            for frame, values in sorted(keys.items()):
                if frame <= pivot:
                    loc = obj.location.copy()
                    for i, v in values.items():
                        loc[i] = v
                    frames.append((offset_to + (pivot - frame), loc))
            out[obj] = frames
        return out


def auto_spec(root, instances, gap=None, step_seconds=0.6, hold=0.15, name="auto"):
    """A teardown worked out from the copy's geometry, in STEVE's explode format: small parts (screws, nuts, washers)
    first, then the rest from the outside in, each along the ±X/±Y/±Z that points away from the assembly's centre,
    `gap` mm (default 35 % of the assembly's size) plus its own size, so parts clear each other."""
    bpy.context.view_layer.update()
    lo, hi = _world_box(instances.values())
    centre, size = (lo + hi) / 2, max(hi - lo)
    gap = size * 0.35 if gap is None else gap
    inverse = root.matrix_world.to_3x3().inverted()
    top = {}
    for key, obj in instances.items():
        top.setdefault(key.split("__")[0], []).append(obj)
    parts = []
    for path, objs in top.items():
        plo, phi = _world_box(objs)
        mid, extent = (plo + phi) / 2, phi - plo
        away = inverse @ (mid - centre)
        axis = max(range(3), key=lambda i: abs(away[i]))
        direction = [0.0, 0.0, 0.0]
        direction[axis] = 1.0 if away[axis] >= 0 else -1.0
        distance = gap * (0.5 + abs(away[axis]) / max(size / 2, 1e-6)) + extent[axis]
        small = max(extent) < size * 0.12
        parts.append((0 if small else 1, -away.length, path, [round(c * distance, 2) for c in direction]))
    parts.sort()
    # The biggest, most central part stays put: it's what the rest come off.
    anchor = max(top, key=lambda p: (_world_box(top[p])[1] - _world_box(top[p])[0]).length)
    groups = []    # [(name, {path: move})] in teardown order
    for small, distance, path, move in parts:
        if path == anchor:
            continue
        name_ = "fasteners" if small == 0 else f"layer {round(-distance / (size * 0.15))}"
        if not groups or groups[-1][0] != name_:
            groups.append((name_, {}))
        groups[-1][1][path] = move
    steps, clock = [], 0.0
    for index, (layer, moves) in enumerate(groups):
        label = "Fasteners out" if layer == "fasteners" else f"Parts, layer {index}"
        steps.append({"name": label, "start": round(clock, 3), "duration": step_seconds, "hold": hold,
                      "moves": moves})
        clock += step_seconds + hold
    return {"name": name, "auto": True, "easing": "ease", "duration": round(clock, 3), "loop": False,
            "steps": steps, "anchor": anchor}
