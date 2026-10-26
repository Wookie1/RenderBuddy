"""RenderBuddy: a STEVE connector that renders Blender scenes on this computer.

    python3 renderbuddy.py pair <CODE>     enroll with a pairing code from STEVE, print the fingerprint
    python3 renderbuddy.py paired           exit code 0 if this computer is paired with STEVE, 1 if not
    python3 renderbuddy.py run             wait for render jobs and do them; turns itself off after
                                            8 idle hours (idle_quit_minutes in config.json, 0 = never;
                                            the app's "Turn off after" menu sets it)
    python3 renderbuddy.py stop            stop a running connector
    python3 renderbuddy.py test <script.py> [alias=design.3mf ...]   render locally, no STEVE involved
    python3 renderbuddy.py info            show the settings, fingerprint and Blender found

Settings and the key live in ~/.renderbuddy (override with RENDERBUDDY_HOME). Jobs go to ~/.renderbuddy/jobs.
"""
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone

from steve_connect import SteveClient, SteveError, fingerprint, load_or_create_key, trusted_keys, verify_message

VERSION = "0.7.0"
HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("RENDERBUDDY_HOME", os.path.expanduser("~/.renderbuddy"))
CONFIG = os.path.join(HOME, "config.json")
STATE = os.path.join(HOME, "state.json")
KEY = os.path.join(HOME, "key.pem")
JOBS = os.path.join(HOME, "jobs")
PIDFILE = os.path.join(HOME, "connector.pid")
STATUS = os.path.join(HOME, "status.json")  # the latest status, for the RenderBuddy app on this computer
SANDBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox.sb")
PANEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel")
IDLE_QUIT_MINUTES = 8 * 60
MAX_LINKS = 4
MAX_UPLOAD = 50 * 1024 * 1024

BLENDER_CANDIDATES = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender"),
    "/usr/bin/blender", "/usr/local/bin/blender", "/opt/homebrew/bin/blender",
]


def log(*args):
    print(datetime.now().strftime("%H:%M:%S"), *args, flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def find_blender(config):
    for path in [config.get("blender"), os.environ.get("BLENDER"), shutil.which("blender"), *BLENDER_CANDIDATES]:
        if path and os.path.exists(path):
            return path
    sys.exit("Blender not found: set \"blender\" in ~/.renderbuddy/config.json to its full path.")


def kit_props():
    return {
        "design_1": {"x-steve": "file", "format": "3mf", "title": "Product",
                     "description": "The product: a STEVE part or design."},
        "kinematics_1": {"type": "string", "maxLength": 20000, "title": "Product motion",
                         "description": "The product's kinematics JSON (read_design detail=\"kinematics\"); its "
                                        "first exploded view is used (else one is worked out). Leave out \"fits\"."},
        "design_2": {"x-steve": "file", "format": "3mf", "title": "In use",
                     "description": "Optional: a design showing the product in use, with an animation."},
        "kinematics_2": {"type": "string", "maxLength": 20000, "title": "In-use motion",
                         "description": "The in-use design's kinematics JSON, with its animation."},
        "brand": {"type": "string", "maxLength": 6000, "title": "Brand",
                  "description": "JSON: name, tagline, headline, subhead, cta, url, captions (3 promo lines), "
                                 "reel_captions (3 short beats), colors {ink, paper, accent, slate} as #RRGGBB, font "
                                 "(Inter, or a font file's path on this computer). Missing keys use the sample brand "
                                 "(inktap)."},
        "options": {"type": "string", "maxLength": 20000, "title": "Options",
                    "description": "JSON: looks [[instance pattern, {color, roughness, metallic, coat}], ...] (first "
                                   "match wins), callouts {instance path: label} for the exploded view, highlights "
                                   "[{parts, color, keys: [[seconds, glow]]}] during the in-use animation, "
                                   "demo_animation (its name), explode (its name), product_view / demo_view / "
                                   "exploded_view (camera directions [x, y, z]), exploded_title."},
        "assets": {"type": "string", "maxLength": 200, "title": "Assets",
                   "description": "Comma-separated, default all: logo, cutouts, hero, social, exploded, reel, promo."},
        "quality": {"type": "string", "enum": ["final", "draft", "preview"], "default": "final", "title": "Quality",
                    "description": "final: full size (about 20-30 min on an M4 Max). draft: half size, fewer samples. "
                                   "preview: quarter size, for checking framing and timing in a few minutes."},
        "device": {"type": "string", "enum": ["gpu", "cpu"], "default": "gpu", "title": "Device"},
    }


def kit_script(inputs):
    """The Blender script a marketing_kit run executes."""
    params = {}
    for key in ("options", "brand"):
        if inputs.get(key):
            try:
                value = json.loads(inputs[key])
            except ValueError as e:
                raise RuntimeError(f"{key} is not valid JSON: {e}")
            if key == "brand":
                params["brand"] = value
            else:
                params.update(value)
    params["quality"] = inputs.get("quality") or "final"
    if inputs.get("assets"):
        params["assets"] = inputs["assets"]
    return "import json\nimport bbkit\n\nbbkit.make_kit(json.loads(%r))\n" % json.dumps(params)


def catalog():
    """Everything this connector offers STEVE."""
    props = {
        "script": {"type": "string", "maxLength": 20000, "title": "Scene script",
                   "description": "Blender Python (bpy) that builds the scene, written like a STEVE Blender document's "
                                  "main.py: 1 unit = 1 mm, Z up, an empty scene with STEVE's defaults. "
                                  "`from links import <alias>` adds the designs below. Longer scripts continue in "
                                  "script_2 and script_3 (joined in order)."},
        "script_2": {"type": "string", "maxLength": 20000, "title": "Scene script, continued"},
        "script_3": {"type": "string", "maxLength": 20000, "title": "Scene script, continued"},
        "mode": {"type": "string", "enum": ["auto", "still", "video", "blend"], "default": "auto", "title": "Output",
                 "description": "auto: a still, plus an MP4 when the scene is animated through a camera. still: only "
                                "the first frame. video: always the MP4. blend: only save the .blend file."},
        "device": {"type": "string", "enum": ["gpu", "cpu"], "default": "gpu", "title": "Device",
                   "description": "Cycles on the Mac's GPU (Metal) or the CPU."},
    }
    for i in range(1, MAX_LINKS + 1):
        props[f"design_{i}"] = {"x-steve": "file", "format": "3mf", "title": f"Design {i}",
                                "description": "A STEVE part or design, sent as its bodies placed as modelled."}
        props[f"kinematics_{i}"] = {"type": "string", "maxLength": 20000, "title": f"Motion {i}",
                                    "description": "Optional: the design's kinematics as JSON, from read_design("
                                                   "document_id, detail=\"kinematics\") (detail.kinematics). Then "
                                                   "<alias>.pose(copy, …) and <alias>.play(copy, \"<animation>\") "
                                                   "move its joints, and <alias>.explode(copy, \"<name>\", "
                                                   "reassemble=True) plays its exploded view (auto=True works "
                                                   "one out when it has none). Leave out \"fits\" to save "
                                                   "space."}
        props[f"alias_{i}"] = {"type": "string", "maxLength": 40, "title": f"Alias {i}",
                               "description": f"Its name in the script (`from links import <alias>`); "
                                             f"default design_{i}."}
    return {
        "actions": [
            {"name": "render_scene", "title": "Render a Blender scene",
             "description": "Builds a scene from a Blender Python script and up to 4 STEVE designs in the local "
                            "Blender, renders it on this computer, and returns still.png, animation.mp4 "
                            "(animated scenes) and scene.blend, with timings in the result.",
             "inputs": {"type": "object", "properties": props, "required": ["script"]},
             "result": {"type": "object"}, "confirm": False},
            {"name": "marketing_kit", "title": "Make a marketing kit",
             "description": "Branded marketing assets for a product, made in the local Blender in one go: logo, "
                            "transparent product cut-outs, a 16:9 hero image, a square social post, a labelled "
                            "exploded view, a 9:16 reel of the product in use, and a 16:9 promo video with captions "
                            "and an end card. Give the product design (with its kinematics, for its exploded view) "
                            "and optionally an in-use design with an animation (a phone tapping, a lid opening).",
             "inputs": {"type": "object", "properties": kit_props(), "required": ["design_1"]},
             "result": {"type": "object"}, "confirm": False},
            {"name": "blender_info", "title": "Blender on this computer",
             "description": "The local Blender version, its GPU devices and the connector's version.",
             "inputs": {"type": "object", "properties": {}}, "result": {"type": "object"}, "confirm": False},
        ],
        "events": [{"name": "status", "description": "idle, rendering (with the current job's progress) or off"}],
    }


# Running Blender -------------------------------------------------------------------------------------------------------

def sandboxed(cmd, job_dir, enabled=True):
    """Wraps a Blender command in sandbox.sb (macOS): no network, writes only to the job folder, no secrets.
    Returns the command and whether it is sandboxed."""
    tool = shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec"
    if not enabled or sys.platform != "darwin" or not os.path.exists(tool) or not os.path.exists(SANDBOX):
        return cmd, False
    real = os.path.realpath
    return [tool, "-f", SANDBOX, "-D", f"JOB={real(job_dir)}", "-D", f"JOBS={real(JOBS)}",
            "-D", f"RBHOME={real(HOME)}", "-D", f"USERHOME={real(os.path.expanduser('~'))}"] + cmd, True


def run_blender(blender, job_dir, script_text, links, mode, device, on_progress=None, procs=None, run_id=None,
                timeout=6 * 3600, kinematics=None, sandbox=True):
    """Render one job in a fresh Blender; returns (report dict, output folder)."""
    out = os.path.join(job_dir, "out")
    os.makedirs(out, exist_ok=True)
    script = os.path.join(job_dir, "main.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(script_text)
    job = os.path.join(job_dir, "job.json")
    save_json(job, {"script": script, "links": links, "kinematics": kinematics or {}, "out": out, "mode": mode,
                    "device": device})
    cmd, is_sandboxed = sandboxed([blender, "-b", "--factory-startup", "--python-exit-code", "1",
                                   "-P", os.path.join(HERE, "blender_runner.py"), "--", job], job_dir, sandbox)
    with open(os.path.join(job_dir, "blender.log"), "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if procs is not None and run_id:
            procs[run_id] = proc
        started = time.time()
        try:
            for line in proc.stdout:
                logf.write(line)
                m = re.match(r"BB_PROGRESS (\d+) (\d+) (.+)", line)
                if m and on_progress:
                    on_progress(int(m.group(1)), int(m.group(2)), m.group(3).strip())
                if time.time() - started > timeout:
                    proc.kill()
                    raise RuntimeError(f"Stopped after {timeout // 3600} hours.")
            proc.wait()
        finally:
            if procs is not None and run_id:
                procs.pop(run_id, None)
    report = load_json(os.path.join(out, "report.json"), None)
    if report is None:
        with open(os.path.join(job_dir, "blender.log"), encoding="utf-8") as f:
            tail = f.read()[-3000:]
        report = {"ok": False, "error": f"Blender exited with code {proc.returncode} before finishing.\n{tail}"}
    report["sandboxed"] = is_sandboxed
    if not is_sandboxed:
        report.setdefault("warnings", []).append("Blender ran without RenderBuddy's sandbox.")
    return report, out


def blender_info(blender):
    probe = ("import bpy, json\n"
             "p = bpy.context.preferences.addons['cycles'].preferences\n"
             "devs = []\n"
             "for b in ('METAL','OPTIX','CUDA','HIP','ONEAPI'):\n"
             "    try:\n        p.compute_device_type = b\n    except TypeError:\n        continue\n"
             "    p.get_devices()\n"
             "    devs += [{'backend': b, 'name': d.name, 'type': d.type} for d in p.devices if d.type == b]\n"
             "print('BB_INFO ' + json.dumps({'blender': bpy.app.version_string, 'gpus': devs}))\n")
    out = subprocess.run([blender, "-b", "--factory-startup", "--python-expr", probe], capture_output=True, text=True,
                         timeout=120).stdout
    info = next((json.loads(l[8:]) for l in out.splitlines() if l.startswith("BB_INFO ")), {"blender": "unknown"})
    info.update({"connector": VERSION, "machine": platform.platform(), "python": platform.python_version(),
                 "sandbox": sandboxed([], JOBS, load_json(CONFIG, {}).get("sandbox", True))[1]})
    return info


# The connector ---------------------------------------------------------------------------------------------------------

class Connector:
    def __init__(self):
        os.makedirs(JOBS, exist_ok=True)
        self.config = load_json(CONFIG, {})
        self.state = load_json(STATE, {"cursor": None, "handled": [], "nonces": []})
        self.key = load_or_create_key(KEY)
        self.client = SteveClient(self.key, self.config.get("base_url") or "https://stevecad.studio/connect/v1", log)
        self.blender = find_blender(self.config)
        self.keys = trusted_keys(self.config["steve_keys"]) if self.config.get("steve_keys") else {}
        self.jobs = queue.Queue()
        self.procs = {}
        self.cancelled = set()
        self.busy = False
        self.last_active = time.time()
        self.lock = threading.Lock()

    @staticmethod
    def code_stamp():
        """When the connector's own files last changed: it restarts itself between jobs to pick up updates."""
        return [os.path.getmtime(os.path.join(HERE, name)) for name in ("renderbuddy.py", "steve_connect.py")]

    def remember(self, run_id, nonce):
        with self.lock:
            self.state["handled"] = (self.state["handled"] + [run_id])[-2000:]
            self.state["nonces"] = (self.state["nonces"] + [nonce])[-5000:]
            save_json(STATE, self.state)

    def pair(self, code):
        if self.config.get("connector"):
            # Pairing again: start over with a new key, so STEVE sees a new connector.
            for path in (KEY, STATE):
                if os.path.exists(path):
                    os.remove(path)
            self.config.pop("connector", None)
            self.key = load_or_create_key(KEY)
            self.client = SteveClient(self.key, self.config.get("base_url") or "https://stevecad.studio/connect/v1",
                                      log)
        answer = self.client.enroll(code.strip().upper(), self.config.get("name") or f"RenderBuddy on {platform.node()}",
                                    VERSION, catalog())
        self.config.update({"connector": answer.get("connector"), "steve_keys": answer.get("steve_keys")})
        save_json(CONFIG, self.config)
        print("\nEnrolled. Fingerprint (paste this into STEVE to confirm):\n")
        print("   ", answer.get("fingerprint") or self.client.fingerprint, "\n")

    def wait_for_confirmation(self):
        """Waits for the person to confirm the fingerprint in STEVE; gives up after 15 minutes."""
        wait = int(os.environ.get("BB_WAIT", "5"))
        self.write_status({"state": "starting"})
        deadline = time.time() + 15 * 60
        tries = 0
        while True:
            try:
                self.client.refresh_token(force=True)
                return
            except SteveError as e:
                if e.code != "pending_confirmation":
                    raise
                if tries == 0:
                    self.write_status({"state": "confirming", "fingerprint": self.client.fingerprint})
                if time.time() > deadline:
                    self.write_status({"state": "off", "why": "not confirmed in STEVE within 15 minutes"})
                    sys.exit("Not confirmed in STEVE within 15 minutes. Open RenderBuddy again to retry.")
                if tries % 12 == 0:
                    log("Waiting for you to confirm this connector in STEVE…")
                tries += 1
                time.sleep(wait)

    def idle_limit(self):
        """Seconds without jobs before turning off (None = never). Read from config.json each time, so a change
        made in the RenderBuddy app applies straight away."""
        minutes = load_json(CONFIG, {}).get("idle_quit_minutes", IDLE_QUIT_MINUTES)
        self.config["idle_quit_minutes"] = minutes
        return float(minutes) * 60 if minutes else None

    @staticmethod
    def write_status(data):
        try:
            save_json(STATUS, {**data, "at": time.time()})
        except OSError:
            pass

    def event(self, data):
        if data.get("state") == "idle":
            data = {**data, "idle_quit_minutes": self.config.get("idle_quit_minutes", IDLE_QUIT_MINUTES)}
        self.write_status(data)
        try:
            self.client.event("status", data)
        except Exception as e:  # events are best-effort
            log("event not sent:", e)

    def publish_panel(self):
        """Puts RenderBuddy's tab in STEVE (the files in panel/), so it shows there with no setup by hand."""
        if not os.path.isdir(PANEL_DIR):
            return
        files = {}
        for name in sorted(os.listdir(PANEL_DIR)):
            with open(os.path.join(PANEL_DIR, name), encoding="utf-8") as f:
                files[name] = f.read()
        try:
            self.client.request("PUT", "/panels/status", {"title": "RenderBuddy", "placement": ["right"],
                                                          "files": files})
        except Exception as e:  # the panel is a nicety: rendering works without it
            log("Panel not published:", e)

    def shut_down(self, why):
        log("Turning off:", why)
        for proc in list(self.procs.values()):
            try:
                proc.terminate()
            except Exception:
                pass
        self.event({"state": "off", "why": why})
        try:
            if open(PIDFILE).read().strip() == str(os.getpid()):
                os.remove(PIDFILE)
        except OSError:
            pass
        sys.exit(0)

    def run(self):
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
        signal.signal(signal.SIGTERM, lambda *_: self.shut_down("asked to stop on this computer"))
        if not self.keys:
            self.keys = trusted_keys(self.client.keys())
        log(f"RenderBuddy {VERSION}, {self.client.fingerprint}, Blender at {self.blender}")
        self.wait_for_confirmation()
        self.client.put_catalog(catalog())
        self.publish_panel()
        threading.Thread(target=self.worker, daemon=True).start()
        self.event({"state": "idle"})
        limit = self.idle_limit()
        log("Connected. Waiting for jobs (Ctrl-C to stop)." +
            (f" Turns itself off after {duration(limit)} without jobs." if limit else ""))
        backoff = 1
        code_stamp = self.code_stamp()
        try:
            self.loop(limit, backoff, code_stamp)
        except KeyboardInterrupt:
            self.shut_down("stopped with Ctrl-C")

    def loop(self, limit, backoff, code_stamp):
        while True:
            new_limit = self.idle_limit()
            if new_limit != limit:
                limit = new_limit
                self.last_active = time.time()  # count the new idle time from now
                log("Now turns itself off " + (f"after {duration(limit)} without jobs." if limit else "never."))
                if not self.busy:
                    self.event({"state": "idle"})
            quiet = self.jobs.empty() and not self.busy
            if quiet and limit and time.time() - self.last_active > limit:
                self.shut_down(f"no jobs for {duration(limit)}")
            if self.jobs.empty() and not self.busy and self.code_stamp() != code_stamp:
                log("The connector's code changed: restarting to load it.")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            try:
                self.client.refresh_token()
                answer = self.client.messages(self.state.get("cursor"))
                for message in answer.get("messages", []):
                    self.handle(message)
                if answer.get("cursor") is not None:
                    with self.lock:
                        self.state["cursor"] = answer["cursor"]
                        save_json(STATE, self.state)
                backoff = 1
            except SteveError as e:
                if e.code in ("token_invalid", "token_expired") or e.status == 401:
                    self.client.token = None
                    continue
                log("STEVE answered:", e)
                time.sleep(min(backoff, 60))
                backoff *= 2
            except (OSError, ValueError) as e:
                log("Connection problem:", e)
                time.sleep(min(backoff, 60))
                backoff *= 2

    def handle(self, message):
        env = message.get("envelope", {})
        if not verify_message(message, self.keys):
            # The key list may have rotated: fetch it once and try again.
            self.keys = trusted_keys(self.client.keys())
            if not verify_message(message, self.keys):
                log("Ignored a message with a bad signature:", message.get("id"))
                return
        if message.get("kind") == "cancel":
            run_id = env.get("run")
            self.cancelled.add(run_id)
            proc = self.procs.get(run_id)
            if proc:
                proc.terminate()
            log("Cancel asked for", run_id)
            return
        run_id = env.get("id")
        if run_id in self.state["handled"]:
            return
        problem = None
        names = {a["name"] for a in catalog()["actions"]}
        action = str(env.get("action", ""))
        if env.get("connector") != self.client.fingerprint:
            problem = "This run is for another connector."
        elif action not in names and action.split(".")[-1] not in names and action.split(":")[-1] not in names:
            problem = f"Unknown action {action}."
        elif env.get("nonce") in self.state["nonces"]:
            return
        elif expired(env.get("expires_at")):
            problem = "The run's envelope has expired."
        if problem:
            self.remember(run_id, env.get("nonce"))
            self.safe_report(run_id, "rejected", error=problem)
            return
        self.remember(run_id, env.get("nonce"))
        self.last_active = time.time()
        self.client.report(run_id, "received", message="Queued on " + platform.node())
        self.jobs.put(env)

    def safe_report(self, run_id, state, **fields):
        try:
            self.client.report(run_id, state, **fields)
        except Exception as e:
            log(f"Could not report {state} for {run_id}:", e)

    def worker(self):
        while True:
            env = self.jobs.get()
            self.busy = True
            run_id = env["id"]
            if run_id in self.cancelled:
                self.safe_report(run_id, "cancelled")
                self.busy = False
                continue
            try:
                self.do_run(env)
            except Exception as e:
                log("Run failed:", run_id, e)
                traceback.print_exc()
                self.safe_report(run_id, "cancelled" if run_id in self.cancelled else "failed", error=str(e)[:4000])
            self.busy = False
            self.last_active = time.time()
            self.event({"state": "idle"})

    def download(self, run_id, value, path):
        if value.get("format") == "3mf" or not value.get("headers"):
            data = self.client.request("GET", value["url"], raw=True, timeout=600)
        else:
            req = urllib.request.Request(value["url"], headers=value.get("headers") or {})
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
        if value.get("bytes") is not None and len(data) != int(value["bytes"]):
            raise RuntimeError(f"{value.get('name')}: size {len(data)} is not the {value['bytes']} STEVE sent.")
        if value.get("sha256") and hashlib.sha256(data).hexdigest() != value["sha256"].lower():
            raise RuntimeError(f"{value.get('name')}: the file's SHA-256 doesn't match.")
        with open(path, "wb") as f:
            f.write(data)

    def do_run(self, env):
        run_id, inputs = env["id"], env.get("inputs") or {}
        action = str(env["action"]).split(".")[-1].split(":")[-1]
        if action == "blender_info":
            self.client.report(run_id, "running", progress=0.5, message="Asking Blender")
            self.client.report(run_id, "succeeded", result=blender_info(self.blender), message="Done")
            return
        job_dir = os.path.join(JOBS, time.strftime("%Y%m%d-%H%M%S-") + re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:24])
        os.makedirs(job_dir, exist_ok=True)
        self.client.report(run_id, "running", progress=0.02, message="Fetching designs")
        links, kinematics = {}, {}
        for i in range(1, MAX_LINKS + 1):
            value = inputs.get(f"design_{i}")
            if not isinstance(value, dict):
                continue
            default_alias = {1: "product", 2: "demo"}.get(i) if action == "marketing_kit" else f"design_{i}"
            alias = (inputs.get(f"alias_{i}") or default_alias or f"design_{i}").strip()
            if not alias.isidentifier():
                raise RuntimeError(f"Alias {alias!r} is not a valid Python name.")
            path = os.path.join(job_dir, f"{alias}.3mf")
            self.download(run_id, value, path)
            links[alias] = path
            if inputs.get(f"kinematics_{i}"):
                try:
                    kin = json.loads(inputs[f"kinematics_{i}"])
                except ValueError as e:
                    raise RuntimeError(f"kinematics_{i} is not valid JSON: {e}")
                kinematics[alias] = kin.get("detail", {}).get("kinematics", kin.get("kinematics", kin))
        last = [0.0, None]

        def on_progress(done, total, stage):
            if time.time() - last[0] < 3 and done != total and stage == last[1]:
                return
            last[0], last[1] = time.time(), stage
            frac = 0.05 + 0.9 * (done / total if total else 0)
            text = f"{stage} {done}/{total}" if total > 1 else stage
            self.safe_report(run_id, "running", progress=round(frac, 3), message=text)
            self.event({"state": "rendering", "run": run_id, "progress": round(frac, 3), "message": text})

        log("Rendering", run_id, "with", list(links) or "no designs")
        if action == "marketing_kit":
            script_text, mode = kit_script(inputs), "outputs"
        else:
            script_text = "".join(inputs.get(k) or "" for k in ("script", "script_2", "script_3"))
            mode = inputs.get("mode", "auto")
        report, out = run_blender(self.blender, job_dir, script_text, links, mode, inputs.get("device", "gpu"),
                                  on_progress, self.procs, run_id, kinematics=kinematics,
                                  sandbox=self.config.get("sandbox", True))
        if run_id in self.cancelled:
            self.safe_report(run_id, "cancelled")
            return
        if not report.get("ok"):
            raise RuntimeError(report.get("error", "Blender failed."))
        self.client.report(run_id, "running", progress=0.97, message="Uploading results")
        types = {".png": "image/png", ".mp4": "video/mp4", ".jpg": "image/jpeg", ".json": "application/json"}
        for kind, name in report.get("outputs", {}).items():
            path = os.path.join(out, name)
            if os.path.getsize(path) > MAX_UPLOAD:
                report.setdefault("warnings", []).append(f"{name} is over 50 MB, kept on the computer at {path}.")
                continue
            self.client.upload(run_id, name, path, types.get(os.path.splitext(name)[1].lower(),
                                                             "application/octet-stream"))
        shown = os.path.join(self.config.get("renders_dir") or os.path.join(HERE, "renders"),
                             os.path.basename(job_dir))
        os.makedirs(shown, exist_ok=True)
        for name in report.get("outputs", {}).values():
            shutil.copy2(os.path.join(out, name), os.path.join(shown, name))
        report["kept_at"] = shown
        report.pop("ok", None)
        self.client.report(run_id, "succeeded", result=report, message=summary(report))
        log("Done", run_id, summary(report))


def duration(seconds):
    """30 minutes, 2 hours, 1 hour 30 minutes."""
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    parts = ([f"{hours} hour" + ("s" if hours != 1 else "")] if hours else []) + \
        ([f"{rest} minute" + ("s" if rest != 1 else "")] if rest or not hours else [])
    return " ".join(parts)


def expired(value):
    if value is None:
        return False
    try:
        when = float(value) if not isinstance(value, str) or value.replace(".", "").isdigit() else \
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return when < datetime.now(timezone.utc).timestamp()


def summary(report):
    t = report.get("timings_s", {})
    if "kit" in report:
        return f"Marketing kit: {len(report.get('outputs', {}))} files in {round(t.get('total', 0))} s " \
               f"on {report.get('device', '?')}"
    parts = []
    if "still" in t:
        parts.append(f"still in {t['still']} s")
    if "video" in t:
        parts.append(f"video ({report.get('video', {}).get('frames')} frames) in {t['video']} s")
    return ", ".join(parts) + f" on {report.get('device', '?')}" if parts else "Saved the .blend file"


def main():
    os.makedirs(HOME, exist_ok=True)
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    command = args[0]
    if command == "pair":
        if len(args) < 2:
            sys.exit("Usage: renderbuddy.py pair <CODE>")
        try:
            Connector().pair(args[1])
        except SteveError as e:
            sys.exit(f"STEVE said: {e}")
    elif command == "paired":
        sys.exit(0 if load_json(CONFIG, {}).get("connector") else 1)
    elif command == "run":
        Connector().run()
    elif command == "stop":
        try:
            pid = int(open(PIDFILE).read().strip())
            os.kill(pid, signal.SIGTERM)
            print("Stopping RenderBuddy (process", pid, ")")
        except (OSError, ValueError):
            print("RenderBuddy isn't running.")
    elif command == "info":
        config = load_json(CONFIG, {})
        print("Fingerprint:", fingerprint(load_or_create_key(KEY)))
        print("Paired:", bool(config.get("connector")))
        blender = find_blender(config)
        print("Blender:", blender)
        print(json.dumps(blender_info(blender), indent=2))
    elif command == "test":
        if len(args) < 2:
            sys.exit("Usage: renderbuddy.py test <script.py> [alias=design.3mf ...] [--mode=still]")
        mode = next((a.split("=", 1)[1] for a in args if a.startswith("--mode=")), "auto")
        links = dict(a.split("=", 1) for a in args[2:] if "=" in a and not a.startswith("--"))
        kinematics = {}
        for k, v in list(links.items()):
            if "+" in v:
                v, kin = v.split("+", 1)
                with open(kin, encoding="utf-8") as f:
                    data = json.load(f)
                kinematics[k] = data.get("detail", {}).get("kinematics", data.get("kinematics", data))
            links[k] = os.path.abspath(v)
        job_dir = os.path.join(JOBS, time.strftime("test-%Y%m%d-%H%M%S"))
        os.makedirs(job_dir)
        with open(args[1], encoding="utf-8") as f:
            script = f.read()
        report, out = run_blender(find_blender(load_json(CONFIG, {})), job_dir, script, links, mode, "gpu",
                                  lambda d, t, s: print(f"  {s} {d}/{t}", flush=True), kinematics=kinematics)
        print(json.dumps(report, indent=2))
        print("Outputs in", out)
    else:
        sys.exit(f"Unknown command {command!r}. Try: pair, paired, run, stop, test, info")


if __name__ == "__main__":
    main()
