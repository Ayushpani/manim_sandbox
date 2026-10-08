"""End-to-end checks against a running sandbox: python tests/smoke_test.py [base_url]

Needs the server running (run.ps1 / run.sh) and Docker. Renders every scene in
examples/ plus edge cases (custom base classes, still images, uploaded files,
sound, error reporting) and prints a pass/fail table.
"""

import base64
import json
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent


def call(path, body=None):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"})
    try:
        return 200, json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def render(code, scenes=None, assets=None, **opts):
    status, data = call("/api/render", {"code": code, "scenes": scenes or [], "assets": assets or [], **opts})
    if status != 200:
        return {"status": "rejected", "error": data["detail"]["error"], "error_line": data["detail"].get("line"), "outputs": []}
    while True:
        _, job = call(f"/api/jobs/{data['job_id']}")
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(1)


def png_bytes(w=64, h=64):
    raw = b"".join(b"\x00" + b"".join(bytes([x * 4 % 256, y * 4 % 256, 160]) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def wav_bytes(seconds=1, rate=8000):
    samples = bytes(128 + int(60 * ((i // 20) % 2 * 2 - 1)) for i in range(rate * seconds))
    return b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + \
        struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8) + b"data" + struct.pack("<I", len(samples)) + samples


b64 = lambda b: base64.b64encode(b).decode()
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="orange"/></svg>'

CASES = []


def case(name, expect, code, **kw):
    CASES.append((name, expect, code, kw))


for f in sorted((ROOT / "examples").glob("*.py")):
    case(f"examples/{f.name} (all scenes)", "ok", f.read_text(), scenes="ALL")

case("custom base class", "ok", '''
from manim import *
class Base(Scene):
    def title(self, s):
        t = Text(s).to_edge(UP); self.play(Write(t)); return t
class Lesson(Base):
    def construct(self):
        self.title("Lesson 1"); self.play(Create(Circle()))
''', expect_scenes=["Lesson"])
case("ChatGPT-style file with config + __main__", "ok", '''
from manim import *
config.background_color = "#1e1e2e"
class Demo(Scene):
    def construct(self):
        self.play(DrawBorderThenFill(Star(color=YELLOW)))
if __name__ == "__main__":
    Demo().render()
''')
case("uploaded PNG + SVG", "ok", '''
from manim import *
class Assets(Scene):
    def construct(self):
        img = ImageMobject("photo.png").scale(3).to_edge(LEFT)
        svg = SVGMobject("logo.svg").to_edge(RIGHT)
        self.play(FadeIn(img), DrawBorderThenFill(svg))
''', assets=[{"name": "photo.png", "data": b64(png_bytes())}, {"name": "logo.svg", "data": b64(SVG)}])
case("sound file", "ok", '''
from manim import *
class Beep(Scene):
    def construct(self):
        self.add_sound("beep.wav")
        self.play(GrowFromCenter(Square()))
''', assets=[{"name": "beep.wav", "data": b64(wav_bytes())}])
case("vertical final", "ok", (ROOT / "examples/vertical_short.py").read_text(), quality="final", orientation="vertical")
case("Python error shows line", "fail", '''from manim import *
class Oops(Scene):
    def construct(self):
        c = Circle()
        self.play(Create(circel))
''', expect_line=5, expect_text="NameError")
case("LaTeX error", "fail", '''from manim import *
class BadTex(Scene):
    def construct(self):
        self.play(Write(MathTex(r"\\notacommand{x}")))
''', expect_text="LaTeX")
case("syntax error", "fail", "from manim import *\nclass A(Scene):\n    def construct(self)\n        pass\n",
     expect_line=3, expect_text="SyntaxError")
case("manimgl code", "fail", "from manimlib import *\nclass A(Scene):\n    def construct(self): pass\n",
     expect_text="ManimGL")
case("no network", "fail", '''from manim import *
import urllib.request
class Net(Scene):
    def construct(self):
        urllib.request.urlopen("https://example.com")
''', expect_text="URLError")


def main():
    failures = 0
    for name, expect, code, kw in CASES:
        scenes = kw.pop("scenes", None)
        if scenes == "ALL":
            scenes = call("/api/scenes", {"code": code})[1]["scenes"]
        expect_scenes = kw.pop("expect_scenes", None)
        expect_line, expect_text = kw.pop("expect_line", None), kw.pop("expect_text", None)
        t = time.time()
        job = render(code, scenes, **kw)
        ok = job["status"] == "done"
        problems = []
        if expect == "ok":
            if not ok:
                problems.append(job["error"])
            elif scenes and len(job["outputs"]) != len(scenes):
                problems.append(f"{len(job['outputs'])}/{len(scenes)} outputs")
            if expect_scenes and [o["scene"] for o in job["outputs"]] != expect_scenes:
                problems.append(f"rendered {[o['scene'] for o in job['outputs']]}")
        else:
            if ok:
                problems.append("expected a failure")
            if expect_text and expect_text not in job["error"]:
                problems.append(f"error was: {job['error']}")
            if expect_line and job.get("error_line") != expect_line:
                problems.append(f"line {job.get('error_line')} != {expect_line}")
        kinds = ", ".join(f"{o['scene']}:{o['kind']}" for o in job["outputs"])
        detail = kinds if expect == "ok" else job["error"]
        print(f"{'PASS' if not problems else 'FAIL'}  {name:45} {time.time() - t:6.1f}s  {'; '.join(problems) or detail}")
        failures += bool(problems)
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
