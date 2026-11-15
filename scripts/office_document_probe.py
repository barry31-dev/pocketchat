#!/usr/bin/env python3
"""Measure only generated PDF/DOCX fixtures; not a production import boundary.

Run with separately installed pypdf, python-docx and reportlab. No input-file
argument is accepted. All outputs are synthetic and the absent destination is
created once. Each parser runs in a child with a wall-clock deadline. This is a
measurement harness, not a sandbox or a validator for arbitrary documents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixtures(root):
    from docx import Document
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from PIL import Image

    items = []
    for name, pages in [('text', 3), ('page-boundary', 32), ('page-over', 33)]:
        path = root / (name + '.pdf')
        writer = canvas.Canvas(str(path), invariant=1)
        for page in range(1, pages + 1):
            writer.drawString(72, 740, 'Project sample page %d' % page)
            writer.drawString(72, 715, 'Revenue %d. Decision: keep the original source.' % (page * 12))
            writer.showPage()
        writer.save()
        items.append((path, dict(format='pdf',pages=pages,text='Project sample page 1')))
    path = root / 'encrypted.pdf'
    writer = PdfWriter()
    writer.append(PdfReader(root / 'text.pdf'))
    writer.encrypt('synthetic-probe-password')
    with path.open('wb') as stream:
        writer.write(stream)
    items.append((path, dict(format='pdf',status='encrypted')))
    path = root / 'image-only.pdf'
    writer = canvas.Canvas(str(path), invariant=1)
    writer.drawImage(ImageReader(Image.new('RGB', (32, 32), 'white')), 72, 700, 32, 32)
    writer.showPage()
    writer.save()
    items.append((path, dict(format='pdf',pages=1,status='no-extracted-text')))
    path = root / 'broken.pdf'
    path.write_bytes(b'%PDF-1.7\nsynthetic incomplete container\n')
    items.append((path, dict(format='pdf',status='parser-error')))
    for name, paragraphs in [('body-table', 3), ('paragraph-boundary', 256), ('paragraph-over', 257)]:
        path = root / (name + '.docx')
        doc = Document()
        for paragraph in range(1, paragraphs + 1):
            doc.add_paragraph('Project paragraph %d. Decision: keep the original source.' % paragraph)
        table = doc.add_table(rows=2, cols=2)
        for cell, text in zip([c for row in table.rows for c in row.cells], ['Month','Revenue','November','36']):
            cell.text = text
        doc.save(path)
        items.append((path, dict(format='docx',paragraphs=paragraphs,cells=4,text='November')))
    original = root / 'body-table.docx'
    path = root / 'external-relationship.docx'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == 'word/_rels/document.xml.rels':
                data = data.replace(b'</Relationships>', b'<Relationship Id="rIdExternalProbe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/probe" TargetMode="External"/></Relationships>')
            output.writestr(info.filename, data)
    items.append((path, dict(format='docx',paragraphs=3,cells=4,text='November',external=True)))
    path = root / 'ratio-risk.docx'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('word/document.xml', b'X' * (1024 * 1024))
    items.append((path, dict(format='docx',inventory_only=True,ratio_over=100)))
    path = root / 'disguised.docx'
    path.write_bytes((root / 'text.pdf').read_bytes())
    items.append((path, dict(format='docx',status='parser-error')))
    return items


def observe(path, kind, inventory_only):
    result = dict(status='extracted',segments=[])
    started = time.perf_counter()
    if kind == 'docx':
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                result['zip'] = dict(entries=len(entries),expanded_bytes=sum(i.file_size for i in entries),
                    largest_entry=max((i.file_size for i in entries),default=0),
                    largest_ratio=max((i.file_size / max(1,i.compress_size) for i in entries),default=0))
        except zipfile.BadZipFile:
            pass
    if inventory_only:
        result['status'] = 'inventory-only'
    else:
        try:
            if kind == 'pdf':
                from pypdf import PdfReader
                reader = PdfReader(path, strict=True)
                if reader.is_encrypted:
                    result['status'] = 'encrypted'
                else:
                    result['pages'] = len(reader.pages)
                    result['segments'] = [dict(kind='page',index=i,text=p.extract_text() or '') for i,p in enumerate(reader.pages,1)]
                    if not any(s['text'].strip() for s in result['segments']):
                        result['status'] = 'no-extracted-text'
            else:
                from docx import Document
                doc = Document(path)
                result['paragraphs'] = len(doc.paragraphs)
                result['cells'] = sum(len(row.cells) for table in doc.tables for row in table.rows)
                result['segments'] = [dict(kind='paragraph',index=i,text=p.text) for i,p in enumerate(doc.paragraphs,1)]
                result['segments'] += [dict(kind='cell',table=t,row=r,column=c,text=cell.text) for t,table in enumerate(doc.tables,1) for r,row in enumerate(table.rows,1) for c,cell in enumerate(row.cells,1)]
                result['external_relationships'] = sum(rel.is_external for rel in doc.part.rels.values())
        except Exception as exc:
            result['status'] = 'parser-error'
            result['error_type'] = type(exc).__name__
    result['elapsed_seconds'] = time.perf_counter() - started
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['peak_rss_bytes'] = rss if sys.platform == 'darwin' else rss * 1024
    except ImportError:
        result['peak_rss_bytes'] = None
    return result


def main():
    if sys.flags.optimize:
        raise SystemExit('Synthetic probe refuses optimization: its acceptance checks require normal mode.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='absent output directory for generated samples')
    args = parser.parse_args()
    args.out.mkdir(parents=False,exist_ok=False)
    import pypdf, docx, reportlab
    report = dict(schema='pocketchat-document-probe.v1',python=platform.python_version(),platform=platform.platform(),
                  libraries=dict(pypdf=pypdf.__version__,python_docx=docx.__version__,reportlab=reportlab.Version),
                  scope='generated known synthetic inputs only; not a production validator or sandbox',samples=[])
    for path, expected in fixtures(args.out):
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(path), expected['format'], str(bool(expected.get('inventory_only')))]
        proc = subprocess.run(command,capture_output=True,text=True,timeout=10)
        if proc.returncode != 0:
            raise RuntimeError('Synthetic parser worker failed: '+path.name)
        observation = json.loads(proc.stdout)
        assert observation['status'] == expected.get('status','inventory-only' if expected.get('inventory_only') else 'extracted'), path.name
        for key in ('pages','paragraphs','cells'):
            if key in expected:
                assert observation[key] == expected[key], path.name
        text = '\n'.join(s['text'] for s in observation['segments'])
        if 'text' in expected:
            assert expected['text'] in text, path.name
        if expected.get('external'):
            assert observation['external_relationships'] == 1
        if 'ratio_over' in expected:
            assert observation['zip']['largest_ratio'] > expected['ratio_over']
        canonical = json.dumps(observation['segments'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        report['samples'].append(dict(name=path.name,raw_bytes=path.stat().st_size,raw_sha256=sha(path.read_bytes()),
            extracted_sha256=sha(canonical),extracted_chars=len(text),expected=expected,observation=observation))
    # A changed source retains the first snapshot and gets a new raw identity.
    original = (args.out / 'text.pdf').read_bytes()
    changed = original + b'\n% synthetic revised snapshot\n'
    (args.out / 'changed.pdf').write_bytes(changed)
    assert sha(original) != sha(changed)
    report['changed_raw_identity'] = dict(original=sha(original),changed=sha(changed),original_preserved=True)
    (args.out / 'probe.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(samples=len(report['samples']),passed=True,libraries=report['libraries'],output='probe.json')))


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--worker':
        print(json.dumps(observe(Path(sys.argv[2]),sys.argv[3],sys.argv[4]=='True')))
    else:
        main()
