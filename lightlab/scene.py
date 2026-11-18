"""Light Lab scene building inside Blender: dark world, physically-based materials matched to body
names, LED emitters (matched bodies or explicit dies) and fixed cameras. 1 Blender unit = 1 mm."""

import glob
import math
import os

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
        _add_absorption(mat, sigma)
    return mat


def _add_absorption(mat, sigma):
    """Beer–Lambert volume absorption, coefficients per mm (Cycles Volume Absorption:
    sigma_channel = (1 - color_channel) × density; 1 Blender unit = 1 mm)."""
    density = max(sigma)
    color = [1.0 - s / density for s in sigma]
    tree = mat.node_tree
    absorb = tree.nodes.new("ShaderNodeVolumeAbsorption")
    absorb.location = (-300, -300)
    absorb.inputs["Color"].default_value = (*color, 1.0)
    absorb.inputs["Density"].default_value = density
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(absorb.outputs["Volume"], output.inputs["Volume"])


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
        elif entry["kind"] == "finish":
            mat = make_finish(name, entry)
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


def make_finish(name, entry):
    """A finish from the Light Lab library (lightlab/finishes.py): a textured material recipe,
    with procedural roughness variation selling the surface. See FINISHES for the recipe keys."""
    params = entry["params"]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = _principled(mat)
    rough = params.get("roughness", 0.4)
    clear_plastic = bool(params.get("transmission"))
    if clear_plastic:
        _set_input(bsdf, ["Transmission Weight", "Transmission"], params["transmission"])
        _set_input(bsdf, ["IOR"], params.get("ior", 1.49))
        _set_input(bsdf, ["Metallic"], 0.0)
        if entry.get("tint"):
            tint_mm = entry.get("tint_mm", 3.0)
            sigma = [-math.log(max(t, 1e-6)) / tint_mm for t in entry["tint"]]
            if max(sigma) > 1e-9:
                _add_absorption(mat, sigma)
    else:
        _set_input(bsdf, ["Base Color"], (*params.get("color", [0.75, 0.76, 0.78]), 1.0))
        _set_input(bsdf, ["Metallic"], params.get("metallic", 0.0))
    _set_input(bsdf, ["Roughness"], rough)
    if params.get("anisotropy"):
        _set_input(bsdf, ["Anisotropic"], params["anisotropy"])
    if params.get("coat"):
        _set_input(bsdf, ["Coat Weight", "Clearcoat"], params["coat"])
    rough_socket = None
    for key, scale, stretch in (("streaks", 6.0, True), ("grain", 350.0, False), ("dust", 80.0, False)):
        if params.get(key):
            rough_socket = _rough_noise(tree, rough_socket, rough, params[key], scale, stretch)
    if rough_socket is not None:
        tree.links.new(rough_socket, bsdf.inputs["Roughness"])
    return mat


def _rough_noise(tree, source, base, amplitude, scale, stretch=False):
    """Roughness = base (constant, first link) or source (a socket, later links), plus a procedurally
    noisy variation with ± amplitude. stretch=True samples the noise through a strongly non-uniform
    mapping: long, thin marks along X, like machining passes."""
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 2.0
    if stretch:
        coordinate = tree.nodes.new("ShaderNodeTexCoord")
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (1.0, 40.0, 40.0)
        tree.links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    centered = tree.nodes.new("ShaderNodeMath")
    centered.operation = "SUBTRACT"
    centered.inputs[1].default_value = 0.5
    tree.links.new(noise.outputs["Fac"], centered.inputs[0])
    scaled = tree.nodes.new("ShaderNodeMath")
    scaled.operation = "MULTIPLY"
    scaled.inputs[1].default_value = amplitude
    tree.links.new(centered.outputs[0], scaled.inputs[0])
    added = tree.nodes.new("ShaderNodeMath")
    added.operation = "ADD"
    if source is None:
        added.inputs[0].default_value = base
    else:
        tree.links.new(source, added.inputs[0])
    tree.links.new(scaled.outputs[0], added.inputs[1])
    return added.outputs[0]


def _resolve_hdri(spec_path):
    """An HDRI named in the spec: a file path on this computer, or one bundled in lightlab/hdris
    (bundled names match loosely: "autoshop_01" finds autoshop_01_1k.hdr)."""
    raw = os.path.expanduser(spec_path)
    if os.path.isfile(raw):
        return raw
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hdris")
    base = os.path.basename(spec_path)
    candidates = [raw, raw + ".hdr", raw + ".exr", os.path.join(folder, base),
                  os.path.join(folder, base + ".hdr"), os.path.join(folder, base + ".exr")]
    candidates += sorted(glob.glob(os.path.join(folder, base + "*")))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SpecError(f"world HDRI {spec_path!r} not found: it is neither a file on this computer nor "
                    f"bundled in {folder}")


def build_world(spec, warnings):
    """The world: 'flat' (a color, the dark default a study wants), 'sunsky' (the physical Nishita)
    or 'envmap' (an HDRI on this computer or bundled)."""
    world_spec = spec["world"]
    world = bpy.data.worlds.new("LightLab")
    world.use_nodes = True
    tree = world.node_tree
    background = tree.nodes["Background"]
    if world_spec["kind"] == "sunsky":
        sky = tree.nodes.new("ShaderNodeTexSky")
        sky.location = (-300, 0)
        # Blender 5 renamed the physical sky: "single" is the Nishita-lineage model (4.x: NISHITA),
        # "multiple" adds multiple scattering for richer horizon colors.
        for candidate in ({"single": ("SINGLE_SCATTERING", "NISHITA"),
                           "multiple": ("MULTIPLE_SCATTERING",)}[world_spec["model"]]):
            try:
                sky.sky_type = candidate
            except TypeError:
                continue
            break
        sky.sun_elevation = math.radians(world_spec["sun_elevation"])
        sky.sun_rotation = math.radians(world_spec["sun_rotation"])
        sky.sun_disc = world_spec["sun_disc"]
        sky.sun_intensity = world_spec["sun_intensity"]
        if hasattr(sky, "air_density"):
            sky.air_density = world_spec["air"]
        if hasattr(sky, "ozone_density"):
            sky.ozone_density = world_spec["ozone"]
        tree.links.new(sky.outputs["Color"], background.inputs["Color"])
    elif world_spec["kind"] == "envmap":
        path = _resolve_hdri(world_spec["file"])
        environment = tree.nodes.new("ShaderNodeTexEnvironment")
        environment.location = (-300, 0)
        environment.image = bpy.data.images.load(path)
        if world_spec["rotation"]:
            coordinate = tree.nodes.new("ShaderNodeTexCoord")
            mapping = tree.nodes.new("ShaderNodeMapping")
            mapping.location = (-600, 0)
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(world_spec["rotation"]))
            tree.links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
        tree.links.new(environment.outputs["Color"], background.inputs["Color"])
        warnings.append(f"world: {os.path.basename(path)} — with an environment world, renders and metrics "
                        f"contain the scene's own light plus the environment's")
    else:
        background.inputs["Color"].default_value = (*world_spec["color"], 1.0)
    background.inputs["Strength"].default_value = world_spec["strength"]
    bpy.context.scene.world = world


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


def build(spec, copies):
    """Builds everything the spec describes. `copies`: {alias: {"instances": {path: obj}}}.
    Returns {cameras: {name: obj}}; warnings accumulate in the given list."""
    warnings = []
    build_world(spec, warnings)
    emitter_hits = apply_materials(spec, copies, warnings)
    build_emitters(spec, emitter_hits, warnings)
    build_explicit_emitters(spec, warnings)
    cameras = build_cameras(spec)
    return {"cameras": cameras, "warnings": warnings}
