"""Exploded view: the design comes apart step by step (its own STEVE exploded view, or one worked out from the
geometry), holds, and goes back together, while the camera drifts slowly around it.
Send with the design as design_1, alias_1 "product", and its kinematics as kinematics_1."""
import math

import bpy
from mathutils import Vector
from links import product

scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.cycles.samples = 16
for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
    try:
        scene.view_settings.look = look
        break
    except TypeError:
        pass

copy = product()
lead_in = 12                                  # half a second assembled first
view = product.exploded(copy)                 # the design's first exploded view (auto when it has none)
lo, hi = view.bounds()                        # everything, fully exploded
floor = view.bounds(upto=0)[0].z              # the bottom as assembled
timeline = product.explode(copy, start_frame=1 + lead_in, hold=1.2, reassemble=True, speed=1.5)
scene.frame_start, scene.frame_end = 1, timeline["end"] + 12


def material(name, color, roughness, metallic=0.0, coat=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Coat Weight"].default_value = coat
    return mat


looks = {
    "powder": material("Black powder coat", (0.06, 0.06, 0.065), 0.55),
    "acrylic": material("White acrylic", (0.82, 0.82, 0.80), 0.15, coat=0.6),
    "paper": material("ePaper", (0.72, 0.72, 0.68), 0.8),
    "steel": material("Zinc screws", (0.75, 0.75, 0.78), 0.3, metallic=1.0),
    "rubber": material("Rubber feet", (0.03, 0.03, 0.03), 0.85),
    "board": material("PCB", (0.05, 0.25, 0.1), 0.5),
    "foam": material("Foam", (0.15, 0.15, 0.16), 0.95),
    "led": material("LED strip", (0.85, 0.8, 0.7), 0.4),
    "sticker": material("NFC sticker", (0.75, 0.55, 0.2), 0.4, metallic=0.6),
}


def look_for(name):
    name = name.lower()
    if "tag_face" in name or "tag_shell" in name or "tag_back" in name:
        return "acrylic"
    if "tag_led" in name:
        return "led"
    if "nfc_sticker" in name:
        return "sticker"
    if "epaper_panel" in name:
        return "paper"
    if "foam" in name:
        return "foam"
    if "foot" in name and "screw" not in name and "insert" not in name:
        return "rubber"
    if any(k in name for k in ("bolt", "screw", "nut", "washer", "insert", "standoff")):
        return "steel"
    if any(k in name for k in ("raspberry", "epaper_hat", "ffc_adapter")):
        return "board"
    return "powder"


for key, obj in product.instances(copy).items():
    if not obj.material_slots:
        obj.data.materials.append(None)
    for slot in obj.material_slots:
        slot.link = "OBJECT"
        slot.material = looks[look_for(key.split("/")[-1])]

centre = (lo + hi) / 2
size = (hi - lo).length

bpy.ops.mesh.primitive_plane_add(size=size * 20, location=(centre.x, centre.y, floor))
bpy.context.active_object.is_shadow_catcher = True

target = bpy.data.objects.new("Camera target", None)
scene.collection.objects.link(target)
target.location = centre
camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
scene.camera = camera
camera.data.lens = 60
camera.data.clip_start, camera.data.clip_end = 1, 100_000
aim = camera.constraints.new("TRACK_TO")
aim.target, aim.track_axis, aim.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
# The camera, high on the front-right, drifts 30 degrees around the product over the clip, at a distance
# that fits the exploded extent.
distance = size * 1.9
for frame, degrees in ((scene.frame_start, -45), (scene.frame_end, -15)):
    a = math.radians(degrees)
    camera.location = centre + Vector((math.cos(a), math.sin(a), 0.8)).normalized() * distance
    camera.keyframe_insert("location", frame=frame)


def area_light(name, offset, strength, light_size):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
    scene.collection.objects.link(light)
    light.location = centre + Vector(offset) * size
    light.rotation_euler = (centre - light.location).to_track_quat("-Z", "Y").to_euler()
    light.data.energy = strength * (centre - light.location).length ** 2
    light.data.size = light_size * size
    return light


area_light("Key", (1.0, -0.9, 1.1), 6, 0.6)
area_light("Fill", (-1.1, -0.6, 0.5), 2, 1.0)
area_light("Rim", (0.0, 1.1, 0.9), 4, 0.4)
world = bpy.data.worlds.new("Studio")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.035, 0.04, 0.05, 1)
scene.world = world
