"""Study spec: validate, normalize, hash. bpy-free, so a spec can be checked before Blender starts
and reused by a future Mitsuba adapter. See LIGHTLAB.md for the spec reference and rationale."""

import hashlib
import json
import re

KINDS = ("opaque", "pmma", "glass", "diffuser", "emitter")
QUALITY = {"preview": {"samples": 32, "scale": 50},
           "draft": {"samples": 128, "scale": 75},
           "final": {"samples": 256, "scale": 100}}


class SpecError(ValueError):
    """A problem with the study spec, reported as the run's error."""


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if low is not None and value < low:
        raise SpecError(f"{name} must be ≥ {low}, got {value}")
    if high is not None and value > high:
        raise SpecError(f"{name} must be ≤ {high}, got {value}")
    return value


def _vec(value, n, name, low=None, high=None):
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise SpecError(f"{name} must be a list of {n} numbers, got {value!r}")
    return [_number(v, f"{name}[{i}]", low, high) for i, v in enumerate(value)]


def _str(value, name, allowed=None):
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{name} must be a non-empty string, got {value!r}")
    if allowed and value not in allowed:
        raise SpecError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _material(entry, index):
    if not isinstance(entry, dict):
        raise SpecError(f"materials[{index}] must be an object")
    name = f"materials[{index}]"
    out = {"match": entry.get("match", "*")}
    if out["match"] != "*":
        try:
            re.compile(out["match"])
        except re.error as e:
            raise SpecError(f"{name}.match is not a valid regular expression: {e}")
    if entry.get("alias"):
        out["alias"] = _str(entry["alias"], f"{name}.alias")
    kind = _str(entry.get("kind", "opaque"), f"{name}.kind", KINDS)
    out["kind"] = kind
    if kind == "opaque":
        out["color"] = _vec(entry.get("color", [0.75, 0.76, 0.78]), 3, f"{name}.color", 0, 1)
        out["roughness"] = _number(entry.get("roughness", 0.4), f"{name}.roughness", 0, 1)
        out["metallic"] = _number(entry.get("metallic", 0.0), f"{name}.metallic", 0, 1)
    elif kind in ("pmma", "glass", "diffuser"):
        out["ior"] = _number(entry.get("ior", 1.49), f"{name}.ior", 1.0, 3.0)
        default_rough = 0.02 if kind in ("pmma", "glass") else 0.15
        out["roughness"] = _number(entry.get("roughness", default_rough), f"{name}.roughness", 0, 1)
        out["transmission"] = _number(entry.get("transmission", 1.0), f"{name}.transmission", 0, 1)
        if entry.get("absorption") is not None:
            out["absorption"] = _vec(entry["absorption"], 3, f"{name}.absorption (per mm)", 0)
        elif entry.get("tint") is not None:
            tint = _vec(entry["tint"], 3, f"{name}.tint", 0, 1)
            tint_mm = _number(entry.get("tint_mm", 3.0), f"{name}.tint_mm", 0.01)
            # Beer–Lambert: T = exp(-sigma · d)  →  sigma = -ln(T) / d  (per mm; 1 Blender unit = 1 mm)
            out["absorption"] = [(-__import__("math").log(max(t, 1e-6)) / tint_mm) for t in tint]
    elif kind == "emitter":
        if entry.get("radiance") is not None:
            out["radiance"] = _number(entry["radiance"], f"{name}.radiance", 0)
        else:
            out["power"] = _number(entry.get("power", 0.5), f"{name}.power (W)", 0)
        if entry.get("kelvin") is not None:
            out["kelvin"] = _number(entry["kelvin"], f"{name}.kelvin", 1000, 20000)
        else:
            out["color"] = _vec(entry.get("color", [1.0, 1.0, 1.0]), 3, f"{name}.color", 0, 1)
    return out


def _emitter(entry, index):
    if not isinstance(entry, dict):
        raise SpecError(f"emitters[{index}] must be an object")
    name = f"emitters[{index}]"
    out = {"at": _vec(entry.get("at"), 3, f"{name}.at")}
    if entry.get("diameter") is not None:
        out["diameter"] = _number(entry["diameter"], f"{name}.diameter", 0.01)
    else:
        out["size"] = _vec(entry.get("size", [3.0, 1.5]), 2, f"{name}.size", 0.01)
    out["normal"] = _vec(entry.get("normal", [0.0, 0.0, 1.0]), 3, f"{name}.normal")
    length = sum(c * c for c in out["normal"]) ** 0.5
    if length < 1e-9:
        raise SpecError(f"{name}.normal must not be a zero vector")
    out["normal"] = [c / length for c in out["normal"]]
    if entry.get("radiance") is not None:
        out["radiance"] = _number(entry["radiance"], f"{name}.radiance", 0)
    else:
        out["power"] = _number(entry.get("power", 0.5), f"{name}.power (W)", 0)
    if entry.get("kelvin") is not None:
        out["kelvin"] = _number(entry["kelvin"], f"{name}.kelvin", 1000, 20000)
    else:
        out["color"] = _vec(entry.get("color", [1.0, 1.0, 1.0]), 3, f"{name}.color", 0, 1)
    return out


def normalize(raw):
    """Validates a raw study spec and fills in every default. Raises SpecError with a plain message."""
    if not isinstance(raw, dict):
        raise SpecError("The study spec must be a JSON object.")
    out = {
        "study": _str(raw.get("study"), "study"),
        "revision": str(raw["revision"]) if raw.get("revision") is not None else "",
        "demo": bool(raw.get("demo", False)),
        "world": {"color": _vec(raw.get("world", {}).get("color", [0.01, 0.01, 0.012]), 3, "world.color", 0, 1),
                  "strength": _number(raw.get("world", {}).get("strength", 1.0), "world.strength", 0)},
        "materials": [_material(e, i) for i, e in enumerate(raw.get("materials", []))],
        "emitters": [_emitter(e, i) for i, e in enumerate(raw.get("emitters", []))],
    }
    cameras = raw.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        raise SpecError("cameras must be a list with at least one camera.")
    names = set()
    out["cameras"] = []
    for i, entry in enumerate(cameras):
        if not isinstance(entry, dict):
            raise SpecError(f"cameras[{i}] must be an object")
        name = _str(entry.get("name"), f"cameras[{i}].name")
        if name in names:
            raise SpecError(f"cameras[{i}]: the name {name!r} is used twice.")
        names.add(name)
        out["cameras"].append({"name": name,
                               "at": _vec(entry.get("at"), 3, f"cameras[{i}].at"),
                               "look_at": _vec(entry.get("look_at", [0.0, 0.0, 0.0]), 3, f"cameras[{i}].look_at"),
                               "lens_mm": _number(entry.get("lens_mm", 50.0), f"cameras[{i}].lens_mm", 1, 200)})
    out["rois"] = []
    for i, entry in enumerate(raw.get("rois", [])):
        if not isinstance(entry, dict):
            raise SpecError(f"rois[{i}] must be an object")
        name = _str(entry.get("name"), f"rois[{i}].name")
        camera = _str(entry.get("camera"), f"rois[{i}].camera")
        if camera not in names:
            raise SpecError(f"rois[{i}].camera {camera!r} is not one of the cameras {sorted(names)}.")
        roi = {"name": name, "camera": camera}
        if entry.get("object"):
            roi["object"] = _str(entry["object"], f"rois[{i}].object")
        elif entry.get("rect") is not None:
            rect = _vec(entry["rect"], 4, f"rois[{i}].rect (x0 y0 x1 y1, y up, 0..1)", 0, 1)
            if not rect[0] < rect[2] or not rect[1] < rect[3]:
                raise SpecError(f"rois[{i}].rect must have x1 > x0 and y1 > y0")
            roi["rect"] = rect
        else:
            raise SpecError(f"rois[{i}] needs either \"object\" (a body name) or \"rect\" (an image rectangle).")
        out["rois"].append(roi)
    render = raw.get("render") or {}
    preset = QUALITY[_str(raw.get("quality", "final"), "quality", QUALITY)]
    out["quality"] = raw.get("quality", "final")
    out["render"] = {
        "samples": int(_number(render.get("samples", preset["samples"]), "render.samples", 1, 16384)),
        "resolution": [int(v) for v in _vec(render.get("resolution", [1280, 720]), 2, "render.resolution", 8)],
        "scale": int(_number(render.get("scale", preset["scale"]), "render.scale", 5, 100)),
        "seed": int(_number(render.get("seed", 2026), "render.seed", 0)),
        "denoise": bool(render.get("denoise", True)),
    }
    out["spec_hash"] = hashlib.sha256(
        json.dumps(out, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return out
