"""Bump, test, commit, and push a tagged release. CI uploads the ZIP."""
from pathlib import Path
import subprocess
import sys
import re

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'major': 0, 'minor': 1, 'patch': 2}


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


if len(sys.argv) != 2 or sys.argv[1] not in KINDS:
    raise SystemExit('Usage: python3 tools/publish.py major|minor|patch')

branch = subprocess.check_output(
    ('git', 'branch', '--show-current'), cwd=ROOT, text=True).strip()
if branch != 'main':
    raise SystemExit('Publish from the main branch')

run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q')
version_file = ROOT / 'VERSION'
parts = [int(part) for part in version_file.read_text().strip().split('.')]
position = KINDS[sys.argv[1]]
parts[position] += 1
for index in range(position + 1, 3):
    parts[index] = 0
version = '.'.join(str(part) for part in parts)
tag = 'v' + version
if subprocess.run(('git', 'rev-parse', '-q', '--verify', 'refs/tags/' + tag),
                  cwd=ROOT, stdout=subprocess.DEVNULL).returncode == 0:
    raise SystemExit('Tag already exists: ' + tag)
version_file.write_text(version + '\n')
readme = ROOT / 'README.md'
readme.write_text(re.sub(r'\*\*Current version:\*\* \[v[0-9]+\.[0-9]+\.[0-9]+\]',
                         '**Current version:** [' + tag + ']',
                         readme.read_text()))
run(sys.executable, 'tools/package.py')
run('git', 'add', '-A')
run('git', 'commit', '-m', 'Release ' + tag)
run('git', 'tag', tag)
run('git', 'push', 'origin', 'main')
run('git', 'push', 'origin', tag)
print('Published ' + tag + '; GitHub Actions will upload the release ZIP.')
