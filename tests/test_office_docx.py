"""Real DOCX bytes preserve report text and independently recorded sources."""
import importlib.util
import io
import unittest
import zipfile
from unittest.mock import patch
from pocketchat.office_docx import render_docx
from pocketchat.office_store import OfficeError


class DocxTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('docx'), 'optional office extra not installed')
    def test_report_is_readable_docx_and_has_source_manifest(self):
        from docx import Document
        data = render_docx('# Weekly report\n\n## Results\nRevenue: **12** [S1]\n- Follow up', [dict(name='notes.txt', sha256='a'*64)])
        document = Document(io.BytesIO(data))
        text = '\n'.join(p.text for p in document.paragraphs)
        self.assertIn('Revenue: 12 [S1]', text)
        self.assertIn('notes.txt', text)
        self.assertIn('a'*64, text)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('word/document.xml', z.namelist())

    def test_missing_dependency_is_a_typed_refusal(self):
        with patch.dict('sys.modules', {'docx': None}), self.assertRaises(OfficeError):
            render_docx('Report', [])
