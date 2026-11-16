"""Node VM runs real material viewer handlers; browser acceptance is separate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from tests._helpers import subprocess_environment

ROOT = Path(__file__).resolve().parents[1]


class MaterialsViewerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable; viewer race coverage not run')
    def test_real_viewer_async_races_and_identity_refusal(self):
        proc = subprocess.run([shutil.which('node'), str(ROOT/'tests/office_materials_browser.cjs')],
                              env=subprocess_environment({'PATH':os.environ.get('PATH',''),
                                                          'OFFICE_MATERIAL_RACE_ONLY':'1'}),
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        self.assertEqual(json.loads(proc.stdout)['cases'], 4)
