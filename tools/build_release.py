"""Build the checked-in React source and assemble a source-only UI release.

This tool copies UI assets only. It never opens accounts, video or match data.
The resulting deployments/ directory is local output excluded from Git.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'ui-versions' / 'react'


def build(skip_build=False):
    if not skip_build:
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm:
            raise RuntimeError('npm is required. Install Node.js and run npm ci in ui-versions/react/frontend.')
        subprocess.run([npm, 'run', 'build'], cwd=SOURCE / 'frontend', check=True)
    manifest = SOURCE / 'static' / 'react-ui' / '.vite' / 'manifest.json'
    if not manifest.is_file():
        raise RuntimeError('Missing Vite manifest; run npm ci and build the frontend first.')
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if 'src/main.jsx' not in entries:
        raise RuntimeError('The frontend manifest is missing src/main.jsx.')
    name = 'react-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    release = ROOT / 'deployments' / name
    release.mkdir(parents=True)
    shutil.copytree(SOURCE / 'templates', release / 'templates')
    shutil.copytree(SOURCE / 'static', release / 'static',
                    ignore=shutil.ignore_patterns('purple.js'))
    # Shared backend scripts are maintained in the root static directory.
    shutil.copytree(ROOT / 'static', release / 'static', dirs_exist_ok=True)
    shutil.copy2(SOURCE / 'page-config.json', release / 'page-config.json')
    active = ROOT / 'deployments' / 'active.json'
    temporary = active.with_name('active-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps({'release': name}, indent=2) + '\n', encoding='utf-8')
    temporary.replace(active)
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='Assemble a release from an existing npm build.')
    args = parser.parse_args()
    print('Prepared local UI release: ' + str(build(args.skip_build)))


if __name__ == '__main__':
    main()
