<p align="center"><img src="app/RenderBuddy-1024.png" width="160" alt="RenderBuddy icon"></p>

# RenderBuddy

RenderBuddy renders your [STEVE](https://stevecad.studio) designs in Blender on your own Mac, instead of on
STEVE's servers. It's a STEVE *connector*: STEVE (or an agent working in STEVE) sends it a Blender scene script
and up to four designs, and RenderBuddy renders them with your Mac's GPU and sends back the pictures, videos and
the `.blend` file.

- **Fast and unlimited:** your own GPU (Metal), no time limit, no 1080p cap.
- **Motion and exploded views:** designs keep their joints and animations, so parts can move, gears turn and
  assemblies explode and come back together.
- **Marketing kits in one go:** logo, product cut-outs, hero image, social post, labelled exploded view, a vertical
  reel and a promo video, all from one request.
- **Only runs when you want it:** turn it on from the app; it turns itself off after 30 minutes without jobs.

## What you need

- A Mac with Apple silicon or Intel, macOS 13 (Ventura) or later.
- [Blender](https://www.blender.org/download/) in your Applications folder (tested with Blender 5.1).
- Apple's Command Line Tools, for Python and for building the app. If you don't have them, the first step below
  asks to install them (or run `xcode-select --install`).
- A STEVE account.

## Install

In Terminal:

    git clone https://github.com/Wookie1/RenderBuddy.git ~/RenderBuddy
    cd ~/RenderBuddy
    bash install.sh       # sets up a private Python environment (.venv) and checks that Blender is found
    bash make_app.sh      # builds RenderBuddy.app into ~/Applications (takes about a minute)

Keep the `RenderBuddy` folder where it is: the app runs the program from there. (No git? Use **Code → Download ZIP**
on GitHub, unzip it, and run the same two `bash` lines from inside the folder.)

## Connect it to STEVE

Open **RenderBuddy** from Applications. The first time, it asks for a pairing code:

1. In STEVE, open **Settings → Connectors** and click **Pair**. Copy the code.
2. Paste it into RenderBuddy and click **Pair**.
3. RenderBuddy copies a fingerprint to your clipboard. Paste it into STEVE to confirm it's your Mac, then allow
   RenderBuddy's actions.

RenderBuddy also adds a **RenderBuddy** tab to STEVE's right pane, which shows whether it's on and what it's
rendering.

## Everyday use

- **Turn on / off:** open the app and click **Turn On** or **Turn Off**. It also turns itself off after 30 minutes
  without jobs. Closing the window doesn't stop it.
- **From a link:** `renderbuddy://start` turns it on (handy as a browser bookmark next to STEVE),
  `renderbuddy://stop` turns it off.
- **Change the idle time:** set `"idle_quit_minutes"` in `~/.renderbuddy/config.json` (0 means never).
- **Log:** the app's **Log** button opens `~/.renderbuddy/connector.log`.
- **Pair again:** the app's **Pair Again** button.
- **Blender somewhere else:** put its path in `~/.renderbuddy/config.json` as `{"blender": "/path/to/Blender"}`.

## Update

    cd ~/RenderBuddy && git pull && bash make_app.sh

## Uninstall

Click **Remove…** in the app (or open `renderbuddy://uninstall`). It moves the app, the program folder and its
settings (`~/.renderbuddy`) to the Trash, and can keep your renders (they move to Movies → RenderBuddy renders).
Then remove the connector in STEVE under **Settings → Connectors**. Nothing else is installed.

## Files

| Path | What it is |
| --- | --- |
| `renderbuddy.py` | The connector: pairs with STEVE, takes jobs, runs Blender (`pair`, `run`, `stop`, `test`, `info`) |
| `steve_connect.py` | Signed requests to STEVE's connector API |
| `blender_runner.py`, `threemf.py`, `steve_motion.py`, `steve_explode.py` | Inside Blender: loads designs, joints, animations and exploded views |
| `bbkit.py`, `bbstate.py` | The marketing-kit studio, layouts and outputs |
| `panel/` | The RenderBuddy tab shown in STEVE |
| `app/` | The Mac app (`RenderBuddy.swift`), its icon, and a simpler fallback (`RenderBuddy.applescript`) |
| `examples/` | Example scene scripts |

Settings, the connector's key, the log and each job's files live in `~/.renderbuddy` (or `$RENDERBUDDY_HOME`).

## Actions

**render_scene**
- `script`: Blender Python, written exactly like a STEVE Blender document's main.py (1 unit = 1 mm, Z up). The
  scene starts empty, with STEVE's defaults: Cycles, 16 samples, denoised, 1280 × 720, 24 fps, frames 1 to 250,
  and AgX.
- `design_1` to `design_4`: STEVE parts or designs, sent as 3MF. `alias_1` to `alias_4` set their names in the
  script (default `design_1`, and so on).
  `from links import stand` then `copy = stand(at=(x, y, z), rotate=("Z", 90), name=...)`,
  `stand.root(copy)`, and `stand.instances(copy)` all work as they do in STEVE.
- `kinematics_1` to `kinematics_4`: optional. Each one is the design's joints and animations as JSON, copied from
  STEVE's `read_design(document_id, detail="kinematics")["detail"]["kinematics"]`. With it, the parts can move:
  `stand.pose(copy, "open")` or `stand.pose(copy, {"hinge": 30})` sets a pose, and
  `stand.play(copy, "tap")` keys one of the design's animations, or a drive, onto the timeline and sets the
  frame range. Joints follow the design's couplings and gear ratios, and chains of joints move together.
  `stand.rig(copy)` returns the rig: `rig.moving["hinge"]` is the empty that moves.
  Exploded views come in the same JSON (`explodes`, from `design.explode(...)` in STEVE):
  `stand.explode(copy, "teardown", hold=1.2, reassemble=True, speed=1.5)` keys it onto the timeline, step by
  step, and returns each step's frames. `stand.exploded(copy).bounds()` is the fully exploded extent, for framing
  the camera. A design without an exploded view gets one worked out from its geometry (`auto=True`).
  `examples/exploded_view.py` is a complete exploded-view video script.
- `script_2` and `script_3`: overflow for long scripts. STEVE allows 20,000 characters per input, so the three
  are joined in order.
- `mode`: `auto` (a still, plus an MP4 if the scene is animated), `still`, `video`, or `blend`.
- `device`: `gpu` (the default) or `cpu`.

It returns `still.png`, `animation.mp4` and `scene.blend` (each up to 50 MB). The result also includes timings,
the device used, the scene settings, and any warnings. Each job's files stay in `~/.renderbuddy/jobs/`, and a copy goes to the `renders/` folder next to the connector
(or `renders_dir` in config.json). The result's `kept_at` says where.

**marketing_kit**: a whole set of branded marketing assets in one request, made by `bbkit.py`:
logo (on dark and on light), transparent product cut-outs, a 16:9 hero image, a square social post, a labelled
exploded view, a 9:16 reel of the product in use, and a 16:9 promo video with captions and an end card.
- `design_1` + `kinematics_1`: the product (its first exploded view is used, or one is worked out).
- `design_2` + `kinematics_2`: optional, the product in use, with an animation (for the reel and the promo).
- `brand`: JSON with name, tagline, headline, subhead, cta, url, captions, reel_captions, colors {ink, paper, accent,
  slate} and font. Anything left out comes from the sample brand, "inktap".
- `options`: JSON with looks (materials by instance pattern), callouts (exploded-view labels), highlights (parts
  that glow during the animation), camera directions and names of the animation and exploded view to use.
- `assets`: which ones (default all); `quality`: final, draft or preview.

Scripts sent to render_scene can use the same pieces: `import bbkit`, then `bbkit.Studio`, `bbkit.Layout`,
`bbkit.make_logo` and `bbstate.output(path)` to hand any file back to STEVE (with `mode: "outputs"`).

**blender_info**: reports the Blender version, the GPUs it found, and the connector's version.

## Differences from STEVE's built-in Blender

- Rendering runs on your machine, with no time limit and no 1080p cap. EEVEE works on the Mac's GPU.
- Designs come in as 3MF: the bodies with their positions and colours. Body names come from the 3MF, so they can
  differ slightly from the instance names in STEVE.
- There are no inspection looks (`inspect_model`) yet. Use `mode: "still"` to get a quick look first.

## Test without STEVE

    .venv/bin/python renderbuddy.py test my_scene.py stand=stand.3mf --mode=still
    .venv/bin/python renderbuddy.py test my_scene.py stand=stand.3mf+stand_kinematics.json   # with motion

## Running it without the app

    .venv/bin/python renderbuddy.py pair CODE    # then confirm the printed fingerprint in STEVE
    .venv/bin/python renderbuddy.py run          # Ctrl-C (or `renderbuddy.py stop`) to stop

## License and trademarks

MIT, see [LICENSE](LICENSE). RenderBuddy is an independent project. It isn't made or endorsed by the Blender
Foundation or by STEVE. Blender is a trademark of the Blender Foundation.
