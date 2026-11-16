"""Bounded data-only DOCX package checks, not text extraction or a sandbox."""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import stat
import struct
import time
import zipfile
import zlib
from xml.sax import SAXException, handler, make_parser
from xml.sax.handler import (feature_external_ges, feature_external_pes,
                             feature_namespaces, property_lexical_handler)

from .office_store import OfficeError

MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MIME = 'application/vnd.openxmlformats-officedocument.'
MAIN_TYPE = MIME + 'wordprocessingml.document.main+xml'
REL_TYPE = 'application/vnd.openxmlformats-package.relationships+xml'
FIXED_TYPES = {
    'word/document.xml': MAIN_TYPE,
    'docProps/core.xml': 'application/vnd.openxmlformats-package.core-properties+xml',
    'docProps/app.xml': MIME + 'extended-properties+xml',
    'word/stylesWithEffects.xml': 'application/vnd.ms-word.stylesWithEffects+xml',
}
for _part in ('styles', 'settings', 'webSettings', 'fontTable', 'numbering'):
    FIXED_TYPES['word/' + _part + '.xml'] = MIME + 'wordprocessingml.' + _part + '+xml'


def _refuse(message):
    raise OfficeError('DOCX package refused: ' + message + '.')


def _part_type(name):
    if name in FIXED_TYPES:
        return FIXED_TYPES[name]
    if re.fullmatch(r'word/theme/theme[1-9][0-9]*\.xml', name):
        return MIME + 'theme+xml'
    if re.fullmatch(r'customXml/item[1-9][0-9]*\.xml', name):
        return 'application/xml'
    if re.fullmatch(r'customXml/itemProps[1-9][0-9]*\.xml', name):
        return MIME + 'customXmlProperties+xml'
    if name == 'docProps/thumbnail.jpeg':
        return 'image/jpeg'
    if name in ('[Content_Types].xml', '_rels/.rels'):
        return 'application/xml' if name == '[Content_Types].xml' else REL_TYPE
    if name == 'word/_rels/document.xml.rels' or re.fullmatch(r'customXml/_rels/item[1-9][0-9]*\.xml\.rels', name):
        return REL_TYPE
    _refuse('unsupported package part')


class _XML(handler.ContentHandler, handler.EntityResolver):
    def __init__(self, name, state):
        super().__init__()
        self.name, self.state = name, state
        self.stack = []
        self.defaults, self.overrides, self.ids = {}, {}, set()
        self.main_links = 0
        self.body_count = 0
        self.pending_namespaces = 0

    def _tick(self):
        if time.monotonic() > self.state['deadline']:
            _refuse('package-check time limit')

    def startPrefixMapping(self, prefix, uri):
        self.pending_namespaces += 1
        if self.pending_namespaces > 64:
            _refuse("XML namespace attribute limit")

    def startElementNS(self, name, qname, attrs):
        self._tick()
        self.state['nodes'] += 1
        if self.state['nodes'] > 100000 or len(self.stack) >= 64:
            _refuse('XML node or depth limit')
        if len(attrs) + self.pending_namespaces > 64:
            _refuse('XML attribute limit')
        self.pending_namespaces = 0
        if not self.stack:
            expected = {'[Content_Types].xml': (CT, 'Types'),
                        'word/document.xml': (W, 'document')}.get(self.name)
            if self.name.endswith('.rels'):
                expected = (REL, 'Relationships')
            if expected and name != expected:
                _refuse('invalid document root or XML package root')
        if self.name == '[Content_Types].xml' and self.stack:
            if len(self.stack) != 1 or name not in ((CT, 'Default'), (CT, 'Override')):
                _refuse('invalid content type declaration')
            attr = dict(attrs.items())
            kind = name[1]
            keyname = 'Extension' if kind == 'Default' else 'PartName'
            if set(attr) != {(None, keyname), (None, 'ContentType')}:
                _refuse('invalid content type attributes')
            key, value = attr[(None, keyname)], attr[(None, 'ContentType')]
            target = self.defaults if kind == 'Default' else self.overrides
            if key in target or len(key) > 200 or len(value) > 200:
                _refuse('duplicate or oversized content type')
            target[key] = value
        elif self.name.endswith('.rels') and self.stack:
            self._relationship(name, attrs)
        if self.name == 'word/document.xml':
            if name[0] == W and name[1] in ('hyperlink', 'altChunk', 'object', 'pict', 'drawing', 'subDoc', 'control', 'fldSimple', 'instrText', 'fldChar'):
                _refuse('active or unsupported document part')
            if name == (W, 'body'):
                if self.stack != [(W, 'document')] or self.body_count:
                    _refuse('invalid document body')
                self.body_count += 1
            if name == (W, 'p') and self.stack and self.stack[-1] == (W, 'body'):
                self.state['paragraphs'] += 1
                if self.state['paragraphs'] > 256:
                    _refuse('body paragraphs exceed 256')
            if name == (W, 'tc'):
                self.state['cells'] += 1
                if self.state['cells'] > 1024:
                    _refuse('table cells exceed 1,024')
        self.stack.append(name)

    def _relationship(self, name, attrs):
        if len(self.stack) != 1 or name != (REL, 'Relationship'):
            _refuse('invalid relationship XML')
        values = dict(attrs.items())
        keys = {(None, k) for k in ('Id', 'Type', 'Target')}
        if not keys <= set(values) or not set(values) <= keys | {(None, 'TargetMode')}:
            _refuse('invalid relationship attributes')
        rid, typ, target = (values[(None, k)] for k in ('Id', 'Type', 'Target'))
        if (not rid or rid in self.ids or len(rid) > 200 or len(target) > 200 or
                values.get((None, 'TargetMode'), 'Internal') != 'Internal'):
            _refuse('external or duplicate relationship')
        self.ids.add(rid)
        if (not target or any(c in target for c in ('\\', ':', '%', '?', '#')) or
                target.startswith('/') or any(x == '' for x in target.split('/'))):
            _refuse('unsafe relationship target')
        base = '' if self.name == '_rels/.rels' else posixpath.dirname(posixpath.dirname(self.name))
        resolved = posixpath.normpath(posixpath.join(base, target))
        if resolved not in self.state['types']:
            _refuse('missing or unsupported relationship target')
        if self.name == '_rels/.rels':
            allowed = {R + 'officeDocument': {'word/document.xml'},
                       R + 'extended-properties': {'docProps/app.xml'},
                       REL + '/metadata/core-properties': {'docProps/core.xml'},
                       REL + '/metadata/thumbnail': {'docProps/thumbnail.jpeg'}}
        elif self.name == 'word/_rels/document.xml.rels':
            allowed = {R + p: {'word/' + p + '.xml'} for p in ('styles', 'settings', 'webSettings', 'fontTable', 'numbering')}
            allowed[R + 'theme'] = {n for n in self.state['types'] if n.startswith('word/theme/')}
            allowed[R + 'customXml'] = {n for n in self.state['types'] if re.fullmatch(r'customXml/item[1-9][0-9]*\.xml', n)}
            allowed['http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'] = {'word/stylesWithEffects.xml'}
        else:
            number = re.fullmatch(r'customXml/_rels/item([1-9][0-9]*)\.xml\.rels', self.name).group(1)
            allowed = {R + 'customXmlProps': {'customXml/itemProps' + number + '.xml'}}
        if resolved not in allowed.get(typ, set()):
            _refuse('unsupported relationship type or target')
        if typ == R + 'officeDocument':
            self.main_links += 1

    def endElementNS(self, name, qname):
        self._tick()
        self.stack.pop()

    def characters(self, content):
        self._tick()  # Never collect or interpret source text.

    def resolveEntity(self, publicId, systemId):
        _refuse('external entity')

    def startDTD(self, name, publicId, systemId):
        _refuse('DTD or entity declaration')

    def endDTD(self): pass
    def startEntity(self, name): _refuse('entity')
    def endEntity(self, name): pass
    def startCDATA(self): pass
    def endCDATA(self): pass
    def comment(self, content): self._tick()


def _xml_check(name, data, state):
    target = _XML(name, state)
    parser = make_parser(['xml.sax.expatreader'])
    parser.setFeature(feature_namespaces, True)
    parser.setFeature(feature_external_ges, False)
    parser.setFeature(feature_external_pes, False)
    parser.setContentHandler(target)
    parser.setEntityResolver(target)
    parser.setProperty(property_lexical_handler, target)
    parser.parse(io.BytesIO(data))
    if name == 'word/document.xml' and target.body_count != 1:
        _refuse('missing document body')
    return target


def _check_extra(extra):
    pos = 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            _refuse('invalid ZIP extra data')
        kind, size = struct.unpack_from('<2H', extra, pos)
        if kind == 1:
            _refuse('unsupported ZIP64 extra data')
        pos += 4 + size
        if pos > len(extra):
            _refuse('invalid ZIP extra size')


def _member_bytes(raw, info, state):
    start, end = state['data_ranges'][info.filename]
    compressed = memoryview(raw)[start:end]
    if info.compress_type == zipfile.ZIP_STORED:
        data = bytes(compressed)
    else:
        # ZipExtFile truncates to file_size; independently validate the raw stream.
        decoder = zlib.decompressobj(-15)
        data = bytearray()
        for offset in range(0, len(compressed), 65536):
            pending = compressed[offset:offset + 65536]
            while pending:
                if time.monotonic() > state['deadline']:
                    _refuse('package-check time limit')
                chunk = decoder.decompress(pending, min(65536, MAX_MEMBER_BYTES + 1 - len(data)))
                data.extend(chunk)
                if len(data) > MAX_MEMBER_BYTES or len(data) > 100 * max(info.compress_size, 1):
                    _refuse('streamed ZIP expansion or ratio limit')
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    _refuse('trailing ZIP DEFLATE data')
        if not decoder.eof:
            _refuse('unfinished ZIP DEFLATE stream')
        data = bytes(data)
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        _refuse('inconsistent ZIP expanded size or CRC')
    return data


def _inventory(raw, archive, state):
    # Reject tolerance/ZIP64 at the package boundary, not only member versions.
    end_at = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
    if end_at < 0 or end_at + 22 > len(raw):
        _refuse('missing ZIP end record')
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment = struct.unpack_from('<4s4H2IH', raw, end_at)
    if disk or cd_disk or disk_count != count or count == 65535 or cd_size == 0xffffffff or cd_offset == 0xffffffff:
        _refuse('unsupported ZIP64 or inconsistent ZIP end record')
    if end_at + 22 + comment != len(raw) or cd_offset + cd_size != end_at or cd_offset != archive.start_dir:
        _refuse('invalid ZIP directory bounds or ZIP64 end record')
    infos = archive.infolist()
    if count != len(infos):
        _refuse('inconsistent ZIP directory member count')
    pos = cd_offset
    for info in infos:
        if pos + 46 > end_at or raw[pos:pos + 4] != b'PK\x01\x02':
            _refuse('invalid ZIP central directory')
        nlen, extra, note = struct.unpack_from('<3H', raw, pos + 28)
        member_disk = struct.unpack_from('<H', raw, pos + 34)[0]
        if member_disk or pos + 46 + nlen + extra + note > end_at:
            _refuse('invalid ZIP central directory bounds')
        _check_extra(raw[pos + 46 + nlen:pos + 46 + nlen + extra])
        pos += 46 + nlen + extra + note
    if pos != end_at:
        _refuse('hidden ZIP directory data')
    if len(infos) > 512:
        _refuse('ZIP members exceed 512')
    names, ranges = set(), []
    total = 0
    for info in infos:
        name = info.filename
        if name != info.orig_filename or name in names:
            _refuse('Duplicate or invalid ZIP name')
        if (len(name) > 200 or '\\' in name or '\x00' in name or
                any(p in ('', '.', '..') for p in name.split('/'))):
            _refuse('unsafe ZIP name')
        names.add(name)
        mode = (info.external_attr >> 16) & 0xffff
        if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            _refuse('ZIP entry is not a regular file')
        if info.flag_bits & 1:
            _refuse('Encrypted ZIP member')
        if info.flag_bits & ~0x800:
            _refuse('unsupported ZIP flags or data descriptor')
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _refuse('unsupported ZIP compression')
        if info.extract_version >= 45:
            _refuse('unsupported ZIP64 or version')
        if info.file_size > MAX_MEMBER_BYTES:
            _refuse('ZIP member exceeds 2 MiB')
        if info.file_size > 100 * max(info.compress_size, 1):
            _refuse('ZIP expansion ratio exceeds 100:1')
        total += info.file_size
        if total > MAX_EXPANDED_BYTES:
            _refuse('expanded ZIP exceeds 8 MiB')
        if info.header_offset < 0 or info.header_offset + 30 > archive.start_dir:
            _refuse('invalid ZIP header')
        header = struct.unpack_from('<4s5H3I2H', raw, info.header_offset)
        sig, _, flags, method, _, _, crc, compressed, expanded, nlen, extra = header
        end = info.header_offset + 30 + nlen + extra + compressed
        if (sig != b'PK\x03\x04' or flags != info.flag_bits or method != info.compress_type or
                crc != info.CRC or compressed != info.compress_size or expanded != info.file_size or end > archive.start_dir):
            _refuse('inconsistent ZIP header or member size')
        local_name = raw[info.header_offset + 30:info.header_offset + 30 + nlen]
        expected_name = info.orig_filename.encode('utf-8' if flags & 0x800 else 'cp437')
        if local_name != expected_name:
            _refuse('inconsistent ZIP local and central name')
        _check_extra(raw[info.header_offset + 30 + nlen:info.header_offset + 30 + nlen + extra])
        state.setdefault('data_ranges', {})[name] = (info.header_offset + 30 + nlen + extra, end)
        ranges.append((info.header_offset, end))
        state['types'][name] = _part_type(name)
    ranges.sort()
    if not ranges or ranges[0][0] != 0 or any(a[1] != b[0] for a, b in zip(ranges, ranges[1:])) or ranges[-1][1] != archive.start_dir:
        _refuse('overlapping or hidden ZIP data')
    if not {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'} <= names:
        _refuse('Required package parts missing')
    return infos, total


def check_docx_package(raw):
    """Return safe package metadata only. Never creates sources or invokes a parser."""
    if not isinstance(raw, bytes) or len(raw) > MAX_RAW_BYTES:
        _refuse('raw file exceeds 4 MiB')
    if not raw.startswith(b'PK\x03\x04'):
        _refuse('invalid DOCX format signature')
    state = dict(deadline=time.monotonic() + 10, types={}, nodes=0, paragraphs=0, cells=0)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos, total = _inventory(raw, archive, state)
            declarations = None
            root_links = 0
            actual_total = 0
            for info in infos:
                data = _member_bytes(raw, info, state)
                actual_total += len(data)
                if actual_total > MAX_EXPANDED_BYTES:
                    _refuse('streamed ZIP expansion total limit')
                if info.filename.endswith('.xml') or info.filename.endswith('.rels'):
                    checked = _xml_check(info.filename, bytes(data), state)
                    if info.filename == '[Content_Types].xml':
                        declarations = checked
                    if info.filename == '_rels/.rels':
                        root_links = checked.main_links
            if root_links != 1:
                _refuse('missing or duplicate main document relationship')
            if actual_total != total:
                _refuse('inconsistent ZIP total')
            for name, typ in state['types'].items():
                if name == '[Content_Types].xml':
                    continue
                declared = declarations.overrides.get('/' + name, declarations.defaults.get(name.rsplit('.', 1)[-1]))
                if declared != typ:
                    _refuse('missing or incompatible content type')
            for name, typ in declarations.overrides.items():
                if not name.startswith('/') or state['types'].get(name[1:]) != typ:
                    _refuse('unsupported content type override')
            if any(ext not in ('xml', 'rels', 'jpeg') or typ != {'xml': 'application/xml', 'rels': REL_TYPE, 'jpeg': 'image/jpeg'}[ext]
                   for ext, typ in declarations.defaults.items()):
                _refuse('unsupported default content type')
    except OfficeError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, struct.error, zlib.error):
        _refuse('invalid ZIP package')
    except (SAXException, ValueError, UnicodeError):
        _refuse('invalid XML package')
    return dict(format='docx', status='package_checked', text_extracted=False,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), members=len(infos),
                expanded_bytes=total, body_paragraphs=state['paragraphs'], table_cells=state['cells'])
