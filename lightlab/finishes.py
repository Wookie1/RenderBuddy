"""Light Lab finish library: named material recipes for real finishes, keyed by name so a study spec
says e.g. {"kind": "finish", "finish": "machined-aluminum"}. Pure data (bpy-free): lightlab/scene.py
turns a recipe into nodes; lightlab/spec.py validates names against this registry.

Recipe keys (all optional; a finish may ignore some):
- color: base color (linear RGB 0..1)
- metallic, roughness: Principled BSDF basics
- anisotropy: directional highlights (Cycles), for machined marks
- streaks: amplitude of coarse, directionally stretched roughness variation (tool marks)
- grain: amplitude of fine roughness variation (surface texture)
- dust: amplitude of subtle roughness blotches (lens and glass realism)
- coat: clear-coat /lacquer weight
- transmission, ior: clear plastics (pmma)

Every finish is color-overridable from the spec, so one "anodized" recipe covers every anodize color."""

FINISHES = {
    "machined-aluminum": {
        "metallic": 1.0, "roughness": 0.30, "color": [0.91, 0.91, 0.93],
        "anisotropy": 0.6, "streaks": 0.10, "grain": 0.03,
    },
    "bead-blasted-aluminum": {
        "metallic": 1.0, "roughness": 0.42, "color": [0.88, 0.88, 0.90],
        "anisotropy": 0.1, "grain": 0.06,
    },
    "anodized": {
        "metallic": 0.9, "roughness": 0.38, "color": [0.32, 0.12, 0.02],
        "coat": 0.15, "grain": 0.05,
    },
    "molded-plastic": {
        "metallic": 0.0, "roughness": 0.42, "color": [0.09, 0.09, 0.10],
        "grain": 0.03,
    },
    "pmma": {
        "transmission": 1.0, "ior": 1.49, "roughness": 0.02, "dust": 0.02,
    },
    "pcb-solder-mask": {
        "metallic": 0.0, "roughness": 0.38, "color": [0.02, 0.09, 0.04],
        "coat": 0.25, "grain": 0.02,
    },
}
