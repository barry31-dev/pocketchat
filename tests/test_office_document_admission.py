"""Data-only OOXML inventory; no optional parser, extraction or network."""
import io
import struct
import unittest
import zipfile
from unittest.mock import patch

from pocketchat.office_document_admission import check_docx_package
from pocketchat.office_store import OfficeError

CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MAIN = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'


def parts(text='Hello 世界', paragraphs=1, cells=1):
    return {
        '[Content_Types].xml': ('<Types xmlns="%s"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                              '<Override PartName="/word/document.xml" ContentType="%s"/></Types>' % (CT, MAIN)).encode(),
        '_rels/.rels': ('<Relationships xmlns="%s"><Relationship Id="rId1" Type="%sofficeDocument" Target="word/document.xml"/></Relationships>' % (REL, R)).encode(),
        'word/document.xml': ('<w:document xmlns:w="%s"><w:body>%s<w:tbl><w:tr>%s</w:tr></w:tbl></w:body></w:document>' %
                              (W, ('<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % text) * paragraphs,
                               '<w:tc><w:p/></w:tc>' * cells)).encode(),
    }


def package(items=None, compression=zipfile.ZIP_STORED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=compression) as z:
        for name, value in (items or parts()).items():
            z.writestr(name, value)
    return out.getvalue()


class DocumentAdmissionTests(unittest.TestCase):
    def refuse(self, raw, reason):
        with self.assertRaisesRegex(OfficeError, reason):
            check_docx_package(raw)

    def test_ordinary_cjk_identity_counts_no_text(self):
        import hashlib
        raw = package()
        with patch('socket.socket', side_effect=AssertionError('network attempted')):
            result = check_docx_package(raw)
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['bytes'], len(raw))
        self.assertEqual(result['status'], 'package_checked')
        self.assertFalse(result['text_extracted'])
        self.assertEqual((result['body_paragraphs'], result['table_cells']), (1, 1))
        self.assertNotIn('Hello', str(result))
        self.assertNotIn('世界', str(result))

    def test_signature_and_raw_size_refused(self):
        self.refuse(b'%PDF-1.7 secret', 'format')
        self.refuse(b'PK\x03\x04' + b'x' * (4 * 1024 * 1024), '4 MiB')
        self.refuse(b'PK\x03\x04broken', 'ZIP')

    def test_traversal_backslash_absolute_and_case_alias(self):
        for name in ('../escape.xml', '/escape.xml', 'word\\escape.xml', 'word/../escape.xml', 'word//escape.xml', 'WORD/document.xml'):
            p = parts(); p[name] = b'<x/>'
            self.refuse(package(p), 'part|name')

    def test_duplicate_names_and_symlink(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            for name, value in parts().items(): z.writestr(name, value)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore'); z.writestr('word/document.xml', b'<x/>')
        self.refuse(out.getvalue(), 'Duplicate')
        p = parts(); info = zipfile.ZipInfo('word/styles.xml'); info.create_system = 3
        info.external_attr = (0o120777 << 16); p[info] = b'elsewhere'
        self.refuse(package(p), 'regular')

    def test_encryption_flag_and_crc_corruption(self):
        raw = bytearray(package())
        # Both local and central headers declare encryption; never decrypt.
        for sig, off in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
            pos = raw.index(sig); struct.pack_into('<H', raw, pos + off, 1)
        self.refuse(bytes(raw), 'Encrypted')
        raw = package(); raw = raw.replace(b'Hello', b'Jello', 1)
        self.refuse(raw, 'ZIP')

    def test_member_count_size_and_ratio(self):
        p = parts(); p.update({'word/junk%d.xml' % i: b'<x/>' for i in range(510)})
        self.refuse(package(p), '512')
        p = parts(); p['word/document.xml'] = b'x' * (2 * 1024 * 1024 + 1)
        self.refuse(package(p), '2 MiB')
        p = parts(text='x' * 200000)
        self.refuse(package(p, zipfile.ZIP_DEFLATED), 'ratio')

    def test_dtd_entities_utf16_and_malformed_xml(self):
        for xml in ('<!DOCTYPE w:document [<!ENTITY x "SECRET">]><w:document xmlns:w="%s">&x;</w:document>' % W,
                    '<!DOCTYPE w:document SYSTEM "https://invalid.example/private"><w:document xmlns:w="%s"/>' % W):
            for encoding in ('utf-8', 'utf-16'):
                p=parts(); p['word/document.xml'] = xml.encode(encoding)
                self.refuse(package(p), 'DTD|entity')
        p=parts(); p['word/document.xml'] = b'<wrong>'
        self.refuse(package(p), 'XML')

    def test_external_and_unknown_relationships(self):
        for suffix in ('Target="https://invalid.example/private" TargetMode="External"',
                       'Target="../secret.xml"', 'Target="file:///private"'):
            p=parts(); p['word/_rels/document.xml.rels'] = ('<Relationships xmlns="%s"><Relationship Id="rId1" Type="%shyperlink" %s/></Relationships>' % (REL, R, suffix)).encode()
            self.refuse(package(p), 'relationship')

    def test_active_parts_content_types_missing_and_wrong_main(self):
        for name in ('word/vbaProject.bin', 'word/embeddings/object.bin', 'word/activeX/a.xml', 'unknown.xml'):
            p=parts(); p[name] = b'<x/>'; self.refuse(package(p), 'part')
        p=parts(); p['[Content_Types].xml'] = p['[Content_Types].xml'].replace(MAIN.encode(), b'application/secret')
        self.refuse(package(p), 'content type')
        p=parts(); del p['_rels/.rels']; self.refuse(package(p), 'Required')
        p=parts(); p['word/document.xml'] = b'<x/>'; self.refuse(package(p), 'document root')

    def test_paragraph_cell_and_xml_structure_limits(self):
        check_docx_package(package(parts(paragraphs=256, cells=1024)))
        self.refuse(package(parts(paragraphs=257)), '256')
        self.refuse(package(parts(cells=1025)), '1,024')
        p=parts(); p['word/document.xml'] = ('<w:document xmlns:w="%s">%s%s</w:document>' % (W, '<x>'*65, '</x>'*65)).encode()
        self.refuse(package(p), 'depth')

    def test_do_not_hide_streamed_expansion_or_unsupported_compression(self):
        p=parts()
        self.refuse(package(p, zipfile.ZIP_BZIP2), 'compression')
        raw = bytearray(package(parts(text='x' * 150000), zipfile.ZIP_DEFLATED))
        # Falsely low central size must not turn expanded text into a valid package.
        pos = raw.index(b'PK\x01\x02', raw.index(b'PK\x01\x02')+1)
        pos = raw.index(b'PK\x01\x02', pos+1)
        struct.pack_into('<I', raw, pos+24, 32)
        self.refuse(bytes(raw), 'ZIP|ratio')

    def test_total_expansion_content_duplicates_and_active_xml(self):
        import random
        block = ''.join(random.Random(42).choices('ab', k=50000))
        p = parts()
        for name in ('word/styles.xml', 'word/settings.xml', 'word/webSettings.xml', 'word/fontTable.xml', 'word/numbering.xml'):
            p[name] = ('<x>' + block * 36 + '</x>').encode()
        self.refuse(package(p, zipfile.ZIP_DEFLATED), '8 MiB')
        p=parts(); p['[Content_Types].xml'] = p['[Content_Types].xml'].replace(b'</Types>', b'<Default Extension="rels" ContentType="application/secret"/></Types>')
        self.refuse(package(p), 'duplicate')
        for tag in ('altChunk', 'object', 'drawing', 'instrText'):
            p=parts(); p['word/document.xml'] = ('<w:document xmlns:w="%s"><w:body><w:%s/></w:body></w:document>' % (W, tag)).encode()
            self.refuse(package(p), 'active')

    def test_time_limit_refuses_without_success(self):
        with patch('pocketchat.office_document_admission.time.monotonic', side_effect=[0, 11]):
            self.refuse(package(), 'time limit')

    def test_hidden_local_data_and_duplicate_root_relationship(self):
        raw = package(); pos = raw.index(b'PK\x01\x02')
        self.refuse(raw[:pos] + b'hidden-secret' + raw[pos:], 'ZIP')
        p=parts(); p['_rels/.rels'] = p['_rels/.rels'].replace(b'</Relationships>',
            ('<Relationship Id="rId2" Type="%sofficeDocument" Target="word/document.xml"/></Relationships>' % R).encode())
        self.refuse(package(p), 'duplicate main')

    def test_corrupt_deflate_has_named_content_free_refusal(self):
        raw=bytearray(package(parts(), zipfile.ZIP_DEFLATED))
        header=struct.unpack_from('<4s5H3I2H', raw, 0)
        start=30 + header[-2] + header[-1]
        raw[start:start + 2] = b'\xff\xff'
        self.refuse(bytes(raw), 'ZIP')

    def test_consistently_forged_expansion_and_unfinished_deflate(self):
        import zlib
        def custom(expanded, declared, finish=True):
            # Build both declarations consistently; no ZipExtFile truncation oracle.
            out=bytearray(); directory=bytearray()
            for name, value in parts().items():
                namebytes=name.encode(); payload=expanded if name=='word/document.xml' else value
                prefix=declared if name=='word/document.xml' else value
                encoder=zlib.compressobj(wbits=-15)
                compressed=encoder.compress(payload)+encoder.flush(zlib.Z_FINISH if finish else zlib.Z_SYNC_FLUSH)
                crc=zlib.crc32(prefix); offset=len(out)
                out.extend(struct.pack('<4s5H3I2H',b'PK\x03\x04',20,0,8,0,0,crc,len(compressed),len(prefix),len(namebytes),0)+namebytes+compressed)
                directory.extend(struct.pack('<4s6H3I5H2I',b'PK\x01\x02',20,20,0,8,0,0,crc,len(compressed),len(prefix),len(namebytes),0,0,0,0,0,offset)+namebytes)
            start=len(out); out.extend(directory)
            out.extend(struct.pack('<4s4H2IH',b'PK\x05\x06',0,0,3,3,len(directory),start,0))
            return bytes(out)
        xml=parts()['word/document.xml']
        self.refuse(custom(xml+b'x'*(3*1024*1024),xml), 'expansion|ratio')
        self.refuse(custom(xml,xml,finish=False), 'DEFLATE|ZIP')

    def test_eocd_counts_disks_and_package_zip64(self):
        raw=package(); pos=raw.rfind(b'PK\x05\x06')
        for offset in (4,6,8,10):
            changed=bytearray(raw); struct.pack_into('<H',changed,pos+offset,1 if offset<8 else 0)
            self.refuse(bytes(changed),'ZIP')
        fields=struct.unpack_from('<4s4H2IH',raw,pos)
        zip64=struct.pack('<4sQ2H2I4Q',b'PK\x06\x06',44,45,45,0,0,3,3,fields[5],fields[6])
        locator=struct.pack('<4sIQI',b'PK\x06\x07',0,pos,1)
        end=struct.pack('<4s4H2IH',b'PK\x05\x06',0,0,65535,65535,0xffffffff,0xffffffff,0)
        self.refuse(raw[:pos]+zip64+locator+end,'ZIP64|ZIP')

    def test_hyperlink_elements_and_namespace_attribute_limit(self):
        for attributes in ('w:anchor="local"','r:id="rMissing"'):
            p=parts();p['word/document.xml']=('<w:document xmlns:w="%s" xmlns:r="%s"><w:body><w:p><w:hyperlink %s/></w:p></w:body></w:document>'%(W,R,attributes)).encode()
            self.refuse(package(p),'hyperlink|active')
        p=parts();p['word/document.xml']=('<w:document xmlns:w="%s" %s><w:body/></w:document>'%(W,' '.join('xmlns:n%d="urn:test%d"'%(i,i) for i in range(65)))).encode()
        self.refuse(package(p),'attribute|namespace')

    def test_local_header_name_must_equal_central_name(self):
        raw = bytearray(package())
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            offset = archive.getinfo('word/document.xml').header_offset
        length = struct.unpack_from('<H', raw, offset + 26)[0]
        self.assertEqual(length, len(b'../x/document.xml'))
        raw[offset + 30:offset + 30 + length] = b'../x/document.xml'
        self.refuse(bytes(raw), 'ZIP.*name')
