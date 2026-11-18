# Bundled HDRIs

These environment maps come from [Poly Haven](https://polyhaven.com) and are released under
[CC0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain, no attribution required).
They are renamed copies of the 1k HDRI versions, kept in the repository so a study spec can light a
scene without internet access:

| File | Poly Haven asset | Typical use |
|---|---|---|
| `lilienstein_1k.hdr` | [Lilienstein](https://polyhaven.com/a/lilienstein) | sunrise/sunset outdoor skies — dusk looks |
| `autoshop_01_1k.hdr` | [Autoshop 01](https://polyhaven.com/a/autoshop_01) | warm garage / workshop interior |
| `brown_photostudio_02_1k.hdr` | [Brown Photostudio 02](https://polyhaven.com/a/brown_photostudio_02) | warm studio backdrop |

To add more, download any 1k `.hdr` (or `.exr`) from Poly Haven (or anywhere), drop the file in this
folder, and name it in the spec's world block (`"kind": "envmap", "file": "name"` — the extension is
optional). The Blender render sandbox has no network, so files must be on disk before the run.
