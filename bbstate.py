"""What one RenderBuddy job has made so far, shared by blender_runner and the scripts it runs (bbkit)."""
import os
import shutil

OUT = None          # the job's output folder (set by blender_runner)
OUTPUTS = {}        # name -> file name in OUT, uploaded to STEVE in this order
WARNINGS = []
DETAILS = {}        # extra facts for report.json (what each asset shows, timings)


def progress(done, total, stage):
    print(f"BB_PROGRESS {done} {total} {stage}", flush=True)


def output(path, name=None):
    """Hand a finished file to STEVE: it's copied into the job's output folder (if it isn't there) and uploaded."""
    name = name or os.path.basename(path)
    target = os.path.join(OUT, name)
    if os.path.abspath(path) != os.path.abspath(target):
        shutil.copyfile(path, target)
    OUTPUTS[os.path.splitext(name)[0]] = name
    return target


def scratch(name):
    """A path for an intermediate file (not uploaded)."""
    folder = os.path.join(OUT, "_work")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)
DEVICE = "CPU"      # what Cycles renders on (set by blender_runner)
