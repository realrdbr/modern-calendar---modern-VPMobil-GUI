"""Exercise upgrade preservation without touching real containers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class UploadUpgradeTests(unittest.TestCase):
    def test_upgrade_keeps_existing_files_and_aborts_on_backup_failure(self):
        script = (Path(__file__).resolve().parents[1] / 'start-all.sh').read_text()
        helper = script.split('preserve_uploads() {', 1)[1].split('\n}\n', 1)[0]
        helper = 'preserve_uploads() {' + helper + '\n}\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'uploads').mkdir()
            (root / 'uploads' / 'existing.pdf').write_text('keep')
            docker = root / 'docker'
            docker.write_text('''#!/bin/bash
if [[ "$1" == compose ]]; then echo old-app; exit 0; fi
if [[ "$1" == cp ]]; then
  [[ "${FAIL_BACKUP:-0}" == 1 ]] && exit 1
  echo replace > "$3/existing.pdf"
  echo recovered > "$3/recovered.zip"
fi
''')
            docker.chmod(0o700)
            env = {**os.environ, 'ROOT_DIR': temp, 'TMPDIR': temp,
                   'PATH': temp + os.pathsep + os.environ['PATH']}
            command = ['bash', '-c', 'set -euo pipefail\n' + helper + 'preserve_uploads']
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'uploads' / 'existing.pdf').read_text(), 'keep')
            self.assertEqual((root / 'uploads' / 'recovered.zip').read_text(), 'recovered\n')
            result = subprocess.run(command, env={**env, 'FAIL_BACKUP': '1'}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / 'uploads' / 'existing.pdf').read_text(), 'keep')
