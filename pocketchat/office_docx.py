"""Optional Word export; text is never interpreted as code, HTML or OOXML."""
import io
import re

from .office_store import MAX_REPLY_BYTES, OfficeError, text_value


def render_docx(content, sources):
    text_value(content, MAX_REPLY_BYTES, 'Report')
    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise OfficeError('Word export requires the optional extra: pip install pocketchat[office]', 409) from exc
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.8)
    section.left_margin = section.right_margin = Inches(0.9)
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2', 'Heading 3'):
        style = doc.styles[name]
        style.font.name = 'Calibri'
        style.font.color.rgb = RGBColor(0, 0, 0)
    doc.styles['Normal'].font.size = Pt(11)
    doc.styles['Normal'].paragraph_format.space_after = Pt(6)
    doc.core_properties.author = 'PocketChat'
    doc.core_properties.title = 'Office report'
    for line in content.splitlines():
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        bullet = re.match(r'^[-*]\s+(.+)$', line)
        if heading:
            p = doc.add_heading(level=len(heading[1]))
            text = heading[2]
        elif bullet:
            p, text = doc.add_paragraph(style='List Bullet'), bullet[1]
        else:
            p, text = doc.add_paragraph(), line
        for i, part in enumerate(text.split('**')):
            run = p.add_run(part)
            run.bold = bool(i % 2)
    if sources:
        doc.add_heading('Source manifest', level=2)
        doc.add_paragraph('These are the supplied source versions, not an independent fact check.')
        for i, source in enumerate(sources, 1):
            doc.add_paragraph('[%s] %s\nSHA-256: %s' % (source.get('citation', 'S%d' % i), source['name'], source['sha256']))
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()
