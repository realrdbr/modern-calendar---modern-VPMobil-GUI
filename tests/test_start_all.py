"""Exercise startup failure cleanup without invoking Docker."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


class StartAllTests(unittest.TestCase):
    def run_script(self, mode, *, fail_up=False, fail_sync=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'start-all.sh', root / 'start-all.sh')
            (root / 'sync-ntfy-users.sh').write_text('exit ' + ('9' if fail_sync else '0') + '\n')
            docker = root / 'docker'
            docker.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$TEST_CALLS"
if [[ " $* " == *" up "* && "$TEST_FAIL_UP" == 1 ]]; then exit 7; fi
if [[ "$*" == "compose ps -a -q ntfy" ]]; then echo test-ntfy-id; fi
exit 0
''')
            docker.chmod(0o755)
            calls = root / 'calls'
            result = subprocess.run(['bash', str(root / 'start-all.sh'), mode],
                                    env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'],
                                         'TEST_CALLS': str(calls), 'TEST_FAIL_UP': str(int(fail_up))},
                                    capture_output=True, text=True, timeout=5)
            return result, calls.read_text()

    def test_failed_start_keeps_containers_and_prints_health_diagnostics(self):
        for mode in ('docker', 'docker-proxy'):
            with self.subTest(mode=mode):
                result, calls = self.run_script(mode, fail_up=True)
                self.assertEqual(result.returncode, 7)
                self.assertNotIn('down', calls)
                self.assertIn('compose logs --no-color --tail=100', calls)
                self.assertIn('inspect --format', calls)
                self.assertIn('Container bleiben', result.stdout)

    def test_failed_sync_keeps_containers(self):
        result, calls = self.run_script('docker', fail_sync=True)
        self.assertEqual(result.returncode, 9)
        self.assertNotIn('down', calls)

    def test_successful_run_retains_existing_shutdown_behavior(self):
        for mode in ('docker', 'docker-proxy'):
            result, calls = self.run_script(mode)
            self.assertEqual(result.returncode, 0)
            self.assertIn('down', calls)
