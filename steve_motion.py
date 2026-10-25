"""STEVE joints, couplings, poses, animations and drives, played in Blender.

A linked design's kinematics (read_design(document_id, detail="kinematics")["detail"]["kinematics"]) rigs a copy:
every moving joint gets an axis empty at its origin, aimed along its axis, and a joint empty under it that turns
(revolute) or slides (slider) or both (cylindrical) along its local Z. The child instance's objects ride on the joint
empty, and joints whose parent is another joint's child hang under that joint, so chains move together.

The 3MF shows the design in its code's pose (every joint's and coupling's `value`), so that pose is the rig's rest.
"""
import math

import bpy
from mathutils import Matrix, Vector


def fcurves(idb):
    """An ID's animation curves, on Blender 4.x (action.fcurves) and 5.x (layered actions) alike."""
    ad = idb.animation_data
    if ad is None or ad.action is None:
        return []
    action = ad.action
    try:
        return list(action.fcurves)
    except AttributeError:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)
        return list(bag.fcurves) if bag else []


def _objects_for(instances, name):
    """The objects of instance `name`: itself, its bodies (name__body) and, for a subassembly, everything under it."""
    return [obj for key, obj in instances.items()
            if key == name or key.startswith(name + "__") or key.startswith(name + "/")]


class Rig:
    def __init__(self, kinematics, root, instances):
        self.kin = kinematics or {}
        self.root = root
        self.joints = {j["name"]: j for j in self.kin.get("joints", []) if j.get("type", "revolute") != "rigid"}
        self.couplings = {c["name"]: c for c in self.kin.get("couplings", [])}
        self.animations = {a["name"]: a for a in self.kin.get("animations", [])}
        self.drives = {d["joint"]: d for d in self.kin.get("drives", [])}
        self.moving = {}       # joint name -> joint empty
        self.missing = []      # joint children with no objects in the 3MF
        owner = {j["child"]: name for name, j in self.joints.items()}
        order, seen = [], set()

        def visit(name):
            if name in seen:
                return
            seen.add(name)
            parent = self.joints[name].get("parent")
            if parent in owner and owner[parent] != name:
                visit(owner[parent])
            order.append(name)

        for name in self.joints:
            visit(name)
        collection = root.users_collection[0]
        bpy.context.view_layer.update()
        for name in order:
            joint = self.joints[name]
            parent = joint.get("parent")
            holder = self.moving[owner[parent]] if parent in owner else root
            axis = Vector(joint.get("axis") or (0, 0, 1)).normalized()
            frame = Matrix.Translation(Vector(joint.get("origin") or (0, 0, 0))) @ \
                axis.to_track_quat("Z", "Y").to_matrix().to_4x4()
            pivot = bpy.data.objects.new(f"{name} axis", None)
            collection.objects.link(pivot)
            pivot.parent = holder
            bpy.context.view_layer.update()
            pivot.matrix_world = root.matrix_world @ frame
            mover = bpy.data.objects.new(name, None)
            collection.objects.link(mover)
            mover.parent = pivot
            mover.rotation_mode = "XYZ"
            bpy.context.view_layer.update()
            objects = _objects_for(instances, joint["child"])
            if not objects:
                self.missing.append(joint["child"])
            for obj in objects:
                world = obj.matrix_world.copy()
                obj.parent = mover
                obj.matrix_world = world
            bpy.context.view_layer.update()
            self.moving[name] = mover
        self.rest = self._resolve({})

    # Values ----------------------------------------------------------------------------------------------------------

    def _resolve(self, targets):
        """Joint name -> (turn degrees, travel mm) for these joint/coupling targets over the code's pose."""
        own = {name: j.get("value") or 0 for name, j in self.joints.items()}
        coupled = {name: c.get("value") or 0 for name, c in self.couplings.items()}
        for key, value in targets.items():
            if key in self.couplings:
                coupled[key] = value
            elif key.split(".")[0] in self.joints:
                own[key] = value
        out = {}
        for name, joint in self.joints.items():
            kind = joint.get("type", "revolute")
            turn = travel = 0.0
            base = own.get(name, 0) or 0
            if kind == "revolute":
                turn = base
            elif kind == "slider":
                travel = base
            else:
                turn = own.get(f"{name}.turn", 0) or 0
                travel = own.get(f"{name}.travel", 0) or 0
            for cname, value in coupled.items():
                gears = self.couplings[cname].get("gears", {})
                if name in gears:
                    if kind == "slider":
                        travel += value * gears[name]
                    else:
                        turn += value * gears[name]
                turn += value * gears.get(f"{name}.turn", 0)
                travel += value * gears.get(f"{name}.travel", 0)
            out[name] = (turn, travel)
        return out

    def _set(self, state, frame=None, interpolation=None):
        for name, (turn, travel) in state.items():
            mover = self.moving.get(name)
            if mover is None:
                continue
            rest_turn, rest_travel = self.rest[name]
            mover.rotation_euler = (0, 0, math.radians(turn - rest_turn))
            mover.location = (0, 0, travel - rest_travel)
            if frame is not None:
                mover.keyframe_insert("rotation_euler", index=2, frame=frame)
                mover.keyframe_insert("location", index=2, frame=frame)
        if frame is not None and interpolation:
            for mover in self.moving.values():
                for curve in fcurves(mover):
                    for point in curve.keyframe_points:
                        if point.co.x == frame:
                            point.interpolation = interpolation

    # Public ----------------------------------------------------------------------------------------------------------

    def pose(self, values, frame=None):
        """Put the copy in a pose: a pose name from the design, or {joint or coupling: value}."""
        if isinstance(values, str):
            values = self.kin.get("poses", {}).get(values, {})
        self._set(self._resolve(values), frame)

    def play(self, name=None, start_frame=None, fps=None, set_range=True, seconds=None):
        """Key a design animation (or a drive, by its joint or coupling name) onto the timeline.

        Returns the last frame. With set_range, the scene's frame range becomes the animation's.
        A drive turns at its speed for `seconds` (default: the scene's current range)."""
        scene = bpy.context.scene
        fps = fps or scene.render.fps / scene.render.fps_base
        start = scene.frame_start if start_frame is None else start_frame
        if name is None:
            name = next(iter(self.animations), None) or next(iter(self.drives), None)
        if name in self.animations:
            anim = self.animations[name]
            interpolation = "LINEAR" if anim.get("easing") == "linear" else "BEZIER"
            targets = {}
            last = start
            for key in anim.get("keys", []):
                values = key.get("values") or {}
                if isinstance(values, str):
                    values = self.kin.get("poses", {}).get(values, {})
                targets.update(values)
                last = start + round(key["time"] * fps)
                self._set(self._resolve(targets), last, interpolation)
            if anim.get("loop"):
                for mover in self.moving.values():
                    for curve in fcurves(mover):
                        curve.modifiers.new("CYCLES")
        elif name in self.drives:
            drive = self.drives[name]
            seconds = seconds or (scene.frame_end - start + 1) / fps
            last = start + round(seconds * fps)
            begin = (self.couplings.get(name) or self.joints.get(name) or {}).get("value") or 0
            self._set(self._resolve({name: begin}), start, "LINEAR")
            self._set(self._resolve({name: begin + drive.get("speed", 60) * seconds}), last, "LINEAR")
        else:
            raise KeyError(f"No animation or drive named {name!r}: animations {list(self.animations)}, "
                           f"drives {list(self.drives)}")
        if set_range:
            scene.frame_start, scene.frame_end = start, max(last - (1 if name in self.drives else 0), start + 1)
        return last
