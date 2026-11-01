# Light Lab — light-transmission studies in RenderBuddy

*Plan and architecture. This document is the source of truth: if a session loses context, read this first,
then `renderbuddy.py` and `lightlab/`. Status checklist is at the bottom.*

## Why

The Bronco tail-light project (STEVE: housing, brake-light lens, halo diffuser, billet insert, PCB) needs
**repeatable, comparable measurements of how LED light transmits through the lens and halo** — to compare
design revisions (relative brightness/uniformity), not compliance photometry. Real PMMA/acrylic optics.

## Decisions (from the 2026-11-01 research + planning session)

| Decision | Choice | Why |
|---|---|---|
| Host | **RenderBuddy** (this repo) | It already ships STEVE designs to local Blender as 3MF meshes, renders on the Mac's GPU (Metal), and is wired into STEVE's connector trust model. No STEP importer needed. |
| Language | **Python** | ~99% of wall time is native code (Cycles/Metal, mesh import, OpenEXR, numpy). RenderBuddy is Python; Mitsuba 3 is Python-first (Dr.Jit JIT). Rust/Go would split the codebase for no measurable gain. "Performant" = numpy everywhere, no per-pixel Python loops, fixed seeds, adaptive sampling off for comparability. |
| Renderer v1 | **Cycles** (Blender 5.1, Metal GPU) | Correct transmission/TIR/volume absorption; fastest path to value. |
| Renderer v2 | **Mitsuba 3** adapter, built only when needed | Spectral + absolute radiometry. Triggers: Cycles caustic noise limits comparisons, or absolute photometry needed. No Mitsuba MCP exists (checked 2026-11); its Python API (`mi.load_dict`) is the AI aid. |
| Fallback | LuxCoreRender 2.11 (BlendLuxCore) | Best caustics convergence if Cycles noise fights us. Not planned. |
| Not chosen | Radiance | Validated photometry but wrong tool for curved TIR lens optics. |
| Fidelity | Relative comparisons | Same materials + settings + seed across revisions ⇒ valid *relative* metrics. RGB (not spectral); absolute candela NOT claimed. |

## Architecture

```
STEVE (assembly, e.g. bronco-tail-light)
   │  run action "light_study": study spec JSON + up to 4 designs (3MF)
   ▼
renderbuddy.py  (connector; downloads designs, generates main.py, runs Blender)
   ▼
blender_runner.py  (mode="outputs": the script makes its own files via bbstate)
   ▼
lightlab/  (runs inside Blender; numpy is bundled with Blender)
   ├─ spec.py      validate + normalize + hash the study spec (bpy-free)
   ├─ scene.py     world, materials (PMMA/diffuser/emitter/opaque), cameras
   ├─ capture.py   render <camera>.png (AgX) + <camera>.exr (Raw linear 32-bit) per camera; resolve ROIs
   ├─ metrics.py   numpy ROI statistics → metrics.json + study.csv  (stats_from_array is bpy-free)
   └─ demo.py      proxy tail-light geometry for testing without STEVE designs
   ▼
Outputs uploaded: <camera>.png, <camera>.exr, rois.json, metrics.json, study.csv, contact.png
```

**Renderer-agnostic contract (the Mitsuba plug-in point):** everything goes through the study spec
(`lightlab/spec.py`). A future `mitsuba_adapter` reads the same spec, converts meshes 3MF→PLY once, renders
EXRs, and feeds arrays into `metrics.stats_from_array`. `metrics.py` and `spec.py` never import bpy.

## The study spec (JSON)

```jsonc
{
  "study": "bronco-tail-light",          // required
  "revision": "lens-pass2",              // optional label, lands in the CSV
  "demo": false,                         // true: build proxy geometry, ignore designs
  "world": {"color": [0.01, 0.01, 0.012], "strength": 1.0},
  "materials": [                         // first match wins on body/instance name (case-insensitive regex)
    {"match": "led",  "kind": "emitter", "power": 0.5, "color": [1.0, 0.03, 0.02]},   // W total, 1 unit = 1 mm
    {"match": "lens", "kind": "pmma", "ior": 1.49, "roughness": 0.02,
     "tint": [0.92, 0.10, 0.12], "tint_mm": 3.0},                                      // or "absorption": [..] per mm
    {"match": "halo", "kind": "diffuser", "ior": 1.49, "roughness": 0.15},
    {"match": "*",    "kind": "opaque", "color": [0.75, 0.76, 0.78], "roughness": 0.4, "metallic": 0.0}
  ],
  "emitters": [                          // optional explicit dies when the PCB has no die bodies
    {"at": [10, 0, 2], "size": [3, 1.5], "normal": [0, 0, 1], "power": 0.5, "color": [1.0, 0.03, 0.02]}
  ],
  "cameras": [                           // required, ≥1
    {"name": "rear", "at": [0, -180, 40], "look_at": [0, 0, 20], "lens_mm": 50}
  ],
  "rois": [                              // optional; "frame" (whole image) always measured
    {"name": "lens",   "camera": "rear", "object": "lens"},          // projected object bbox
    {"name": "center", "camera": "rear", "rect": [0.35, 0.35, 0.65, 0.65]}   // normalized image rect
  ],
  "render": {                            // optional; defaults by quality preset below
    "samples": 256, "resolution": [1280, 720], "seed": 2026, "denoise": true
  },
  "quality": "final"                     // preview 32smp/50% · draft 128/75% · final 256/100% (spec.render wins)
}
```

Material kinds: `opaque` (color/roughness/metallic), `pmma` (transmission 1, IOR, roughness, Beer–Lambert
absorption from `tint` at `tint_mm` or direct `absorption` per mm), `diffuser` (frosted transmission),
`emitter` (matched bodies glow; `power` in W distributed over matched surface area, Lambertian). Optional
`kelvin` instead of `color` on emitters. `alias` field on any entry restricts it to one linked design.

## Physics conventions (documented, consistent across runs)

- 1 Blender unit = 1 mm (STEVE convention). Emitter `power` is watts under this convention; emission radiance
  = P / (A·π), with A converted mm²→m². EXR values are relative within this convention — comparisons only.
- Volume absorption: per-channel σₐ (1/mm) used directly as Cycles volume density (per BU = per mm).
- Comparability: fixed `seed`, adaptive sampling **off**, indirect clamp **off** (caustics survive), generous
  transmission bounces (24), same denoiser for PNG and EXR.

## Metrics (per camera × ROI, on linear luminance Y = 0.2126R + 0.7152G + 0.0722B)

mean · median · p10/p90/p95/p99 · max · min · cov (std/mean) · uniformity (p10/p90) · hotspot (p99/median).
Written to `metrics.json` (full) and one `study.csv` row per camera×ROI (append-comparable across revisions;
keyed by study, revision, spec_hash, samples, seed, device).

## Workflow

The STEVE session updates the assembly → runs the **Light transmission study** action on the new revision →
RenderBuddy renders + measures → `study.csv` rows compare revisions bar-by-bar. PNGs (per camera + contact
sheet) come back inline in STEVE; EXR/CSV/JSON are downloadable files.

## Honest limits

Relative engineering comparisons, not FMVSS/ECE photometry. RGB, not spectral (fine for mono-color red/amber;
same-material revisions compare validly). Cycles TIR light pipes converge slowly — 256+ samples, denoised.
If the lens is ever FDM-printed, layer-line scattering dominates reality and this fidelity is more than enough.

## Status

- [x] Plan documented (this file)
- [x] `lightlab/` package: spec, scene, capture, metrics, demo
- [x] `light_study` action in renderbuddy.py (catalog, do_run, summary, `study` CLI)
- [x] Demo spec `examples/lightlab_demo.json`
- [x] End-to-end test through local Blender (`python3 renderbuddy.py study examples/lightlab_demo.json`)
      — verified 2026-11-01 on an M4 Max: ~6 s for two cameras at 96 samples; red PMMA absorption visible in
      the channel means (lens ROI r 0.235 / g 0.004 / b 0.006); hotspots and uniformity measured
- [x] History CSV per study accumulates across runs (`~/.renderbuddy/studies/<study>.csv`)
- [x] App controls: **Features** toggles in RenderBuddy.app (scene renders / marketing kits / light studies) —
      writes `features` into config.json; the connector filters its catalog and re-declares it live
- [ ] First real study on the Bronco assembly (needs the STEVE session's PCB + assembly revisions)
- [ ] Mitsuba adapter (deferred, see triggers above)
- [ ] Optional: RenderBuddy panel charting metric history across revisions

## Testing without STEVE

    .venv/bin/python renderbuddy.py study examples/lightlab_demo.json          # proxy geometry, no designs
    .venv/bin/python renderbuddy.py study myspec.json assembly=path/to.3mf     # real designs
    .venv/bin/python renderbuddy.py info                                       # Blender + GPU check

Outputs land in `~/.renderbuddy/jobs/study-<date>/out/` and the accumulated comparison rows in
`~/.renderbuddy/studies/<study>.csv`.
