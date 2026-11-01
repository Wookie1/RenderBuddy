"""Light Lab demo: a proxy tail light (PCB, three LED dies, housing, PMMA lens slab, halo ring) so a
study can be tested end to end without STEVE designs. Bodies are named for the demo spec's matches."""

import bpy


def _box(name, size, location):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def build():
    """Builds the proxy geometry in the empty scene (millimetres, Z up). Returns {name: object}."""
    bodies = {}
    bodies["pcb_board"] = _box("pcb_board", (90, 40, 1.6), (0, 0, 0))
    for x, name in ((-25, "led_die_left"), (0, "led_die_center"), (25, "led_die_right")):
        bodies[name] = _box(name, (3, 1.5, 1.0), (x, 0, 1.8))
    bodies["housing"] = _box("housing", (110, 60, 20), (0, 0, -11))
    bodies["brake_light_lens"] = _box("brake_light_lens", (96, 46, 3), (0, 0, 5.0))
    bpy.ops.mesh.primitive_torus_add(major_radius=34, minor_radius=2.5, location=(0, 0, 6.5),
                                     major_segments=48, minor_segments=16)
    ring = bpy.context.active_object
    ring.name = "halo_ring"
    ring.scale = (1.0, 1.0, 0.6)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bodies["halo_ring"] = ring
    return bodies
