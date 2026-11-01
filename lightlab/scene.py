"""Light Lab scene building inside Blender: dark world, physically-based materials matched to body
names, LED emitters (matched bodies or explicit dies) and fixed cameras. 1 Blender unit = 1 mm."""

import math

import bpy
import numpy as np
from mathutils import Vector

from .spec import SpecError


def _set_input(node, names, value):
    """Sets the first input socket that exists (socket names moved between Blender versions)."""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return True
    return False


def _principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def _emission_color(tree, entry, location=(-300, 0)):
    """A color for an emitter: linear RGB, or a Blackbody node for kelvin. Returns the socket to link."""
    if entry.get("kelvin") is not None:
        blackbody = tree.nodes.new("ShaderNodeBlackbody")
        blackbody.location = location
        blackbody.inputs["Temperature"].default_value = entry["kelvin"]
        return blackbody.outputs["Color"]
    color = tree.nodes.new("ShaderNodeRGB")
    color.location = location
    color.outputs[0].default_value = (*entry["color"], 1.0)
    return color.outputs[0]


def make_opaque(name, entry):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = _principled(mat)
    _set_input(bsdf, ["Base Color"], (*entry["color"], 1.0))
    _set_input(bsdf, ["Roughness"], entry["roughness"])
    _set_input(bsdf, ["Metallic"], entry["metallic"])
    return mat


def make_glass(name, entry, frosted=False):
    """PMMA/acrylic: full transmission, IOR, optional Beer–Lambert volume absorption (per mm)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = _principled(mat)
    _set_input(bsdf, ["Base Color"], (1.0, 1.0, 1.0, 1.0))
    _set_input(bsdf, ["Roughness"], entry["roughness"] if frosted else min(entry["roughness"], 0.05))
    _set_input(bsdf, ["Transmission Weight", "Transmission"], entry["transmission"])
    _set_input(bsdf, ["IOR"], entry["ior"])
    sigma = entry.get("absorption")
    if sigma and max(sigma) > 1e-9:
        density = max(sigma)
        # Cycles Volume Absorption: sigma_channel = (1 - color_channel) · density
        color = [1.0 - s / density for s in sigma]
        absorb = mat.node_tree.nodes.new("ShaderNodeVolumeAbsorption")
        absorb.location = (-300, -300)
        absorb.inputs["Color"].default_value = (*color, 1.0)
        absorb.inputs["Density"].default_value = density
        output = next(n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
        mat.node_tree.links.new(absorb.outputs["Volume"], output.inputs["Volume"])
    return mat


def make_emitter(name, entry, radiance):
    """An area emitter: radiance in W/(m²·sr) for a Lambertian die, 1 Blender unit = 1 mm."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    for node in list(tree.nodes):
        if node.type != "OUTPUT_MATERIAL":
            tree.nodes.remove(node)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-200, 0)
    _set_input(emission, ["Strength"], radiance)
    tree.links.new(_emission_color(tree, entry), emission.inputs["Color"])
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _world_area(obj):
    """The object's world-space surface area in mm² (1 Blender unit = 1 mm)."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    count = len(mesh.loop_triangles)
    if not count:
        return 0.0
    verts = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    verts = verts @ matrix[:3, :3].T + matrix[:3, 3]
    tris = np.empty(count * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    tris = tris.reshape(-1, 3)
    cross = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def _matches(pattern, text):
    return pattern == "*" or re_search(pattern, text)


def re_search(pattern, text):
    import re
    return re.search(pattern, text, re.IGNORECASE) is not None


def _assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def apply_materials(spec, copies, warnings):
    """Assigns materials to bodies, first matching entry wins (spec order = priority), so a "*"
    catch-all last never overrides an earlier match. Returns the emitter entries that matched bodies,
    as [(entry, [(alias, path, obj)])]; their materials are built by build_emitters."""
    claimed = set()            # id(obj) of bodies an earlier entry already took
    emitter_hits = []
    for index, entry in enumerate(spec["materials"]):
        hits = []
        for alias, group in copies.items():
            if entry.get("alias") and entry["alias"] != alias:
                continue
            for path, obj in group["instances"].items():
                if id(obj) in claimed:
                    continue
                if _matches(entry["match"], path):
                    hits.append((alias, path, obj))
        if not hits and entry["match"] != "*":
            warnings.append(f"materials[{index}]: nothing matched {entry['match']!r}")
            continue
        claimed.update(id(obj) for _, _, obj in hits)
        if entry["kind"] == "emitter":
            emitter_hits.append((entry, hits))
            continue
        name = f"ll_{entry['kind']}_{index}"
        if entry["kind"] == "opaque":
            mat = make_opaque(name, entry)
        elif entry["kind"] == "diffuser":
            mat = make_glass(name, entry, frosted=True)
        else:                      # pmma / glass
            mat = make_glass(name, entry)
        for _, _, obj in hits:
            _assign(obj, mat)
    return emitter_hits


def build_emitters(spec, emitter_hits, warnings):
    """Matched-body emitters: the entry's power is spread over the matched surface (uniform radiance),
    Lambertian: radiance = P / (π · A), with A converted mm² → m² (1 Blender unit = 1 mm)."""
    for index, (entry, hits) in enumerate(emitter_hits):
        if entry.get("radiance") is not None:
            radiance = entry["radiance"]
        else:
            areas = [_world_area(obj) for _, _, obj in hits]
            total_mm2 = sum(areas)
            if total_mm2 <= 1e-9:
                warnings.append(f"materials[{index}]: emitter bodies have no surface area; skipped")
                continue
            radiance = entry["power"] / (total_mm2 * 1e-6 * math.pi)
        mat = make_emitter(f"ll_emit_{index}", entry, radiance)
        for _, _, obj in hits:
            _assign(obj, mat)


def build_explicit_emitters(spec, warnings):
    """Small emissive planes from the spec's emitters list, for when the PCB has no die bodies."""
    for index, entry in enumerate(spec["emitters"]):
        if entry.get("diameter") is not None:
            width = height = entry["diameter"]
        else:
            width, height = entry["size"]
        mesh = bpy.data.meshes.new(f"ll_die_{index}")
        mesh.from_pydata([(-width / 2, -height / 2, 0), (width / 2, -height / 2, 0),
                          (width / 2, height / 2, 0), (-width / 2, height / 2, 0)],
                         [], [(0, 1, 2), (0, 2, 3)])
        mesh.validate()
        mesh.update()
        obj = bpy.data.objects.new(f"ll_die_{index}", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = entry["at"]
        normal = Vector(entry["normal"])
        obj.rotation_euler = normal.to_track_quat("Z", "Y").to_euler()
        if entry.get("radiance") is not None:
            radiance = entry["radiance"]
        else:
            radiance = entry["power"] / (width * height * 1e-6 * math.pi)
        _assign(obj, make_emitter(f"ll_die_mat_{index}", entry, radiance))


def build_cameras(spec):
    """One camera object per spec camera. Returns {name: object}."""
    scene = bpy.context.scene
    cameras = {}
    for entry in spec["cameras"]:
        data = bpy.data.cameras.new(entry["name"])
        data.lens = entry["lens_mm"]
        data.sensor_width = 36.0
        data.clip_start, data.clip_end = 1.0, 200_000.0
        obj = bpy.data.objects.new(entry["name"], data)
        scene.collection.objects.link(obj)
        obj.location = entry["at"]
        direction = Vector(entry["look_at"]) - Vector(entry["at"])
        if direction.length < 1e-6:
            raise SpecError(f"camera {entry['name']!r}: at and look_at are the same point")
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        cameras[entry["name"]] = obj
    return cameras


def build_world(spec):
    world = bpy.data.worlds.new("LightLab")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (*spec["world"]["color"], 1.0)
    background.inputs["Strength"].default_value = spec["world"]["strength"]
    bpy.context.scene.world = world


def build(spec, copies):
    """Builds everything the spec describes. `copies`: {alias: {"instances": {path: obj}}}.
    Returns {cameras: {name: obj}}; warnings accumulate in the given list."""
    warnings = []
    build_world(spec)
    emitter_hits = apply_materials(spec, copies, warnings)
    build_emitters(spec, emitter_hits, warnings)
    build_explicit_emitters(spec, warnings)
    cameras = build_cameras(spec)
    return {"cameras": cameras, "warnings": warnings}
