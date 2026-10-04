"""Build a release archive from the checked-in AMYboard sketch files."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', VERSION):
    raise SystemExit('VERSION must contain MAJOR.MINOR.PATCH')

FILES = (
    'loop_random.py', 'loop_patterns.py', 'loop_engine.py', 'loop_sets.py',
    'loop_preset_preferences.py', 'loop_ui.py', 'loop_hardware.py',
    'sequencer_app.py', 'sketch.py', 'README.md', 'SEQUENCER.md', 'LICENSE',
    'VERSION', 'assets/looper-preview.png', 'assets/looper-bam.png',
    'assets/looper-load.png', 'assets/looper-drum-roll.png',
    'assets/looper-screensaver.gif',
)

archive = ROOT / 'dist' / ('loop-freaker-amyboard-v%s.zip' % VERSION)
archive.parent.mkdir(exist_ok=True)
with ZipFile(archive, 'w', ZIP_DEFLATED) as output:
    for name in FILES:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit('Missing release file: ' + name)
        output.write(path, name)
print(archive)
