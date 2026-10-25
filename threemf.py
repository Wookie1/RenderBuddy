"""A small 3MF reader for the packages STEVE sends (bodies placed as modelled): no Blender needed.

read_3mf(path) -> {"objects": {id: Object}, "items": [Item]} where every Item is one placed body with its
name, its 4x4 world matrix (row-major, millimetres) and the id of the mesh object it shows.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
MATERIAL = "{http://schemas.microsoft.com/3dmanufacturing/material/2015/02}"


class Object:
    def __init__(self, oid, name):
        self.id, self.name = oid, name
        self.vertices = None          # (n, 3) float
        self.triangles = None         # (m, 3) int
        self.colors = None            # (m, 4) float RGBA per triangle, or None
        self.components = []          # [(object id, 4x4 matrix)]


def _matrix(text):
    """3MF's 12-number transform (column-major 3x4) as a 4x4 row-major matrix."""
    m = np.identity(4)
    if text:
        v = [float(x) for x in text.split()]
        m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
        m[:3, 3] = v[9:12]
    return m


def _color(text):
    """#RRGGBB or #RRGGBBAA (sRGB) to linear RGBA floats."""
    text = text.lstrip("#")
    rgba = [int(text[i:i + 2], 16) / 255 for i in range(0, len(text), 2)]
    if len(rgba) == 3:
        rgba.append(1.0)
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgba[:3]]
    return lin + [rgba[3]]


def _model_paths(zf):
    """The root model part first, then any others (production extension splits objects across parts)."""
    names = zf.namelist()
    root = "3D/3dmodel.model"
    try:
        rels = zf.read("_rels/.rels").decode("utf-8")
        found = re.search(r'Target="/?([^"]+\.model)"', rels)
        if found:
            root = found.group(1)
    except KeyError:
        pass
    return [root] + [n for n in names if n.endswith(".model") and n != root]


def read_3mf(path):
    objects, items, palettes = {}, [], {}
    with zipfile.ZipFile(path) as zf:
        for part_index, part in enumerate(_model_paths(zf)):
            tree = ET.fromstring(zf.read(part))
            resources = tree.find(CORE + "resources")
            if resources is None:
                continue
            for group in resources:
                gid = group.get("id")
                if group.tag == CORE + "basematerials":
                    palettes[gid] = [_color(b.get("displaycolor", "#BFBFBFFF")) for b in group.findall(CORE + "base")]
                elif group.tag == MATERIAL + "colorgroup":
                    palettes[gid] = [_color(c.get("color", "#BFBFBFFF")) for c in group.findall(MATERIAL + "color")]
            for node in resources.findall(CORE + "object"):
                obj = Object(node.get("id"), node.get("name") or f"object_{node.get('id')}")
                mesh = node.find(CORE + "mesh")
                if mesh is not None:
                    verts = mesh.find(CORE + "vertices")
                    tris = mesh.find(CORE + "triangles")
                    obj.vertices = np.array([[float(v.get("x")), float(v.get("y")), float(v.get("z"))]
                                             for v in verts], dtype=np.float64).reshape(-1, 3)
                    tri_nodes = list(tris)
                    obj.triangles = np.array([[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))]
                                              for t in tri_nodes], dtype=np.int64).reshape(-1, 3)
                    default_pid, default_p = node.get("pid"), node.get("pindex")
                    if default_pid in palettes or any(t.get("pid") for t in tri_nodes[:1]):
                        colors = []
                        for t in tri_nodes:
                            pid = t.get("pid", default_pid)
                            p = t.get("p1", default_p)
                            pal = palettes.get(pid)
                            colors.append(pal[int(p)] if pal and p is not None and int(p) < len(pal)
                                          else [0.6, 0.6, 0.6, 1.0])
                        obj.colors = np.array(colors)
                comps = node.find(CORE + "components")
                if comps is not None:
                    for c in comps.findall(CORE + "component"):
                        obj.components.append((c.get("objectid"), _matrix(c.get("transform"))))
                objects[obj.id] = obj
            if part_index == 0:
                build = tree.find(CORE + "build")
                for item in (build if build is not None else []):
                    items.append((item.get("objectid"), _matrix(item.get("transform"))))

    # Flatten components into placed mesh bodies, each named by the object nearest the build item that names it.
    placed = []

    def walk(oid, matrix, name, depth=0):
        obj = objects.get(oid)
        if obj is None or depth > 16:
            return
        own = obj.name if obj.name and not obj.name.startswith("object_") else name
        if obj.vertices is not None and len(obj.vertices):
            placed.append({"name": own or obj.name, "object": oid, "matrix": matrix})
        for child, m in obj.components:
            child_name = objects[child].name if child in objects else own
            walk(child, matrix @ m, child_name if len(obj.components) > 1 else own, depth + 1)

    for oid, matrix in items:
        walk(oid, matrix, objects[oid].name if oid in objects else None)
    return {"objects": objects, "items": placed}


if __name__ == "__main__":
    import sys
    data = read_3mf(sys.argv[1])
    print(len(data["objects"]), "objects;", len(data["items"]), "placed bodies")
    for it in data["items"][:80]:
        o = data["objects"][it["object"]]
        print(f"  {it['name']:40s} tris={len(o.triangles):7d} colors={'yes' if o.colors is not None else 'no'}"
              f" at={np.round(it['matrix'][:3, 3], 2).tolist()}")
