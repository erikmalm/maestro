"""Archive startup identities stay opaque and do not initialize source storage."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ArchiveHealthTests(unittest.TestCase):
    def check_health(self, code):
        with tempfile.TemporaryDirectory(prefix="maestro-archive-health-") as directory:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MAESTRO_DATA_DIR": directory, "MAESTRO_CONTEXT_ARCHIVE_DIR": ""},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_health_distinguishes_archive_changes_and_equivalent_paths_without_creating_them(self):
        self.check_health(r'''
import json, os, re
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import app
client = TestClient(app)
def identity():
    response = client.get('/health')
    assert response.status_code == 200
    value = response.json()
    assert re.fullmatch('[a-f0-9]{64}', value['archive_id'])
    return value
os.environ.pop('MAESTRO_CONTEXT_ARCHIVE_DIR', None)
blank = identity()
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = ''
assert identity() == blank
root = Path(os.environ['MAESTRO_DATA_DIR']) / 'not-created-source-archive'
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = str(root)
configured = identity()
assert configured['archive_id'] != blank['archive_id']
alias = str(root) + os.sep + '.'
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = alias.upper() if os.name == 'nt' else alias
assert identity() == configured
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = str(root.parent / 'another-source-archive')
assert identity()['archive_id'] != configured['archive_id']
assert str(root) not in json.dumps(configured)
assert configured['workspace_id'] == blank['workspace_id']
assert not root.exists()
assert not (root.parent / 'another-source-archive').exists()
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = 'relative/source-archive'
invalid_relative = identity()
os.chdir(root.parent)
assert identity() == invalid_relative
assert invalid_relative['archive_id'] != blank['archive_id']
assert not (root.parent / 'relative').exists()
''')

    def test_unresolvable_archive_identity_is_stable_and_does_not_echo_the_path_or_error(self):
        self.check_health(r'''
import json, os, re
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app
client = TestClient(app)
raw = 'synthetic-unresolvable-source-root'
os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = raw
with patch('backend.app.Path', side_effect=OSError('synthetic private path error')):
    first = client.get('/health').json()
with patch('backend.app.Path', side_effect=ValueError('different path diagnostic')):
    repeated = client.get('/health').json()
    os.environ['MAESTRO_CONTEXT_ARCHIVE_DIR'] = 'another-unresolvable-root'
    changed = client.get('/health').json()
assert re.fullmatch('[a-f0-9]{64}', first['archive_id'])
assert first == repeated
assert changed['archive_id'] != first['archive_id']
assert raw not in json.dumps(first)
assert 'private path error' not in json.dumps(first)
assert 'diagnostic' not in json.dumps(repeated)
''')


if __name__ == "__main__":
    unittest.main()
