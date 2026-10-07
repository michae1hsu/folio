import base64
import hashlib
import io
import json
import tempfile
import threading
import unicodedata
import unittest
import zipfile
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image

from backend import config, jobs
from backend.converters import convert_pdf, text_pdf, prepare_document, prepare_page_previews, DOCUMENT_CONVERSION_VERSION
from backend.models import parse_document, validate_document


def document_result(count=2):
    return {'page_count': count, 'integration': 'Page 1 pages提出背景；Page 2 pages呈現結果。',
            'pages': [{'number': n, 'purpose': f'Page {n} pages的功能', 'markdown': f'完整頁面 {n} 內容',
                       'layout': '標題在上，正文依序排列。',
                       'issues': [], 'checks': dict(text=True, tables=True, visuals=True, footnotes=True)}
                      for n in range(1, count + 1)]}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_patch = patch.object(config, 'DATA', Path(self.temp.name))
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)

    def test_complete_pages(self):
        self.assertEqual(len(validate_document(document_result(), 2)['pages']), 2)

    def test_missing_duplicate_and_reordered_pages_are_not_success(self):
        for numbers in ([1], [1, 1], [2, 1], [1, 3]):
            data = document_result()
            data['pages'] = [dict(data['pages'][0], number=n) for n in numbers]
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                validate_document(data, 2)

    def test_empty_content_and_missing_integration_fail(self):
        for field in ('markdown', 'purpose', 'layout'):
            data = document_result()
            data['pages'][1][field] = ' '
            with self.assertRaises(ValueError):
                validate_document(data, 2)
        data = document_result()
        data['integration'] = ''
        with self.assertRaises(ValueError):
            validate_document(data, 2)

    def test_one_request_contains_only_complete_pdf_in_high_detail_and_instructions(self):
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='completed', output_text=json.dumps(document_result()), id='test', usage=None)
        pdf = b'%PDF-whole-file-page1-and-page2'
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        result = parse_document(client, pdf, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['model'], config.DOCUMENT_MODEL)
        content = calls[0]['input'][0]['content']
        self.assertEqual([c['type'] for c in content], ['input_file', 'input_text'])
        self.assertEqual(base64.b64decode(content[0]['file_data'].split(',')[1]), pdf)
        self.assertEqual(content[0]['filename'], 'complete-document.pdf')
        self.assertEqual(content[0]['detail'], 'high')
        self.assertIn('with 2 pages', content[1]['text'])
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(result['pages'], document_result()['pages'])
        self.assertIn('integration', result)
        self.assertEqual(result['input_method'], 'complete_pdf')
        self.assertEqual(result['version'], 3)

    def test_pdf_without_a_text_layer_still_uses_the_complete_pdf(self):
        from unittest.mock import Mock
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(
            status='completed', output_text=json.dumps(document_result()), id='test', usage=None)
        result = parse_document(client, b'%PDF-scanned-pages', 2)
        client.responses.create.assert_called_once()
        self.assertEqual(len(client.responses.create.call_args.kwargs['input'][0]['content']), 2)
        self.assertEqual(result['pages'], document_result()['pages'])

    def test_pdf_size_limit_is_checked_before_calling_the_api(self):
        from unittest.mock import Mock
        client = Mock()
        with self.assertRaisesRegex(ValueError, '50 MB'):
            parse_document(client, b'x' * 50_000_000, 1)
        client.responses.create.assert_not_called()

    def test_pdf_request_does_not_apply_the_old_explicit_image_count_limit(self):
        from unittest.mock import Mock
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(status='incomplete', output_text='')
        with self.assertRaisesRegex(ValueError, 'Model output is incomplete'):
            parse_document(client, b'%PDF-whole-file', 1501)
        client.responses.create.assert_called_once()
        self.assertEqual(len(client.responses.create.call_args.kwargs['input'][0]['content']), 2)

    def test_truncated_response_does_not_trigger_continuation(self):
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='incomplete', output_text='')
        with self.assertRaises(ValueError):
            parse_document(SimpleNamespace(responses=SimpleNamespace(create=create)), b'pdf', 2)
        self.assertEqual(len(calls), 1)

class DrivePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(config, 'DATA', self.root / 'data')
        self.data_patch.start()
        config.DATA.mkdir()
        self.jobs_patch = patch.object(jobs, 'JOBS', {})
        self.jobs_patch.start()

    def tearDown(self):
        self.jobs_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def import_items(self, members):
        scan = {'folder_id': 'folderABCDEFGHIJK', 'folder_name': '測試資料夾',
                'recursive': True, 'account': 'test@example.iam.gserviceaccount.com', 'excluded_folders': [],
                'files': [{'name': name, 'size': len(content), 'source_error': None,
                           'drive': {'id': f'file{n:012}', 'version': '1', 'name': name}}
                          for n, (name, content) in enumerate(members)]}
        result = jobs.import_drive(scan)
        job = jobs.JOBS[result['id']]
        for item, (_, content) in zip(job['files'], members):
            (jobs.folder(job) / item['id'] / ('source' + Path(item['name']).suffix)).write_bytes(content)
            item.update(source_downloaded=True, sha256=hashlib.sha256(content).hexdigest())
        return job

    def test_chinese_paths_and_output_names_are_preserved(self):
        job = self.import_items([('教學/簡報.pdf', b'pdf'), ('教學/簡報.txt', b'text')])
        self.assertEqual(job['files'][0]['name'], '教學/簡報.pdf')
        for item in job['files']:
            jobs.update(job, item, status='failed', error='test')
            jobs.write_markdown(job, item)
        output = jobs.make_archive(job)
        with zipfile.ZipFile(output) as archive:
            self.assertIn('markdown/教學/簡報.pdf.md', archive.namelist())
            self.assertIn('markdown/教學/簡報.txt.md', archive.namelist())
            self.assertIn('manifest.json', archive.namelist())

    def test_media_report_failure_retry_preserves_transcript_and_does_not_transcribe_twice(self):
        job = self.import_items([('完整會議.wav', b'whole audio')])
        item = job['files'][0]
        root = jobs.folder(job) / item['id']
        report = {'text':'# Complete report\n已決議事項與所有關鍵數據。', 'model':'gpt-6-luna'}
        with patch.object(jobs, 'prepare_audio', return_value=(root/'source.wav', 30)), \
             patch.object(jobs, 'transcribe', return_value='完整逐字稿，不重複轉寫。') as transcribe, \
             patch.object(jobs, 'summarize_transcript', side_effect=[ValueError('report failed'), report]) as summarize:
            jobs.run_file(job, item, object())
            self.assertEqual(item['status'], 'failed')
            self.assertEqual(item['pages'][0]['status'], 'completed')
            self.assertTrue((root/'transcript.md').exists())
            jobs.run_file(job, item, object())
            self.assertIn(item['status'], jobs.DONE)
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(summarize.call_count, 2)
            result = (root/'result.md').read_text(encoding='utf-8')
            self.assertIn('Complete report', result)
            self.assertNotIn('完整逐字稿，不重複轉寫', result)
            self.assertEqual(item['api_calls'], 3)
            jobs.ensure_report(job, item, object())
            self.assertEqual(summarize.call_count, 2)

    def test_report_upgrade_uses_saved_transcript_and_caches_current_version(self):
        job = self.import_items([('meeting.mp3', b'audio')])
        item = job['files'][0]
        root = jobs.folder(job) / item['id']
        transcript = 'I spent three days in the field. The plan was 146 pits; we completed 153.'
        (root / 'transcript.md').write_text(transcript, encoding='utf-8')
        (root / 'report.json').write_text(json.dumps({'text': 'old report', 'model': 'old'}), encoding='utf-8')
        with patch.object(jobs, 'summarize_transcript', return_value={'text': '新報告', 'model': 'gpt-6-luna'}) as summarize, \
             patch.object(jobs, 'transcribe') as transcribe:
            jobs.ensure_report(job, item, object())
            jobs.ensure_report(job, item, object())
        self.assertEqual(summarize.call_count, 1)
        self.assertEqual(summarize.call_args.args[1], transcript)
        transcribe.assert_not_called()
        self.assertEqual(item['api_calls'], 1)
        self.assertEqual(json.loads((root / 'report.json').read_text(encoding='utf-8'))['version'], jobs.REPORT_VERSION)

    def test_unknown_format_gets_markdown_and_manifest_error(self):
        job = self.import_items([('unknown.bin', b'abc')])
        client = SimpleNamespace(close=lambda: None)
        with patch.object(jobs, 'api_client', return_value=client):
            jobs.run_job(job)
        self.assertEqual(job['status'], 'partial')
        self.assertTrue(job['files'][0]['has_output'])
        self.assertIn('Unsupported', (jobs.folder(job) / '0001/result.md').read_text(encoding='utf-8'))

    def test_files_really_run_in_parallel_with_one_request_each(self):
        job = self.import_items([('a.pdf', b'complete a'), ('b.pdf', b'complete b')])
        calls = []
        barrier = threading.Barrier(2, timeout=5)
        def parse(client, pdf, count, usage_scope=None):
            calls.append(pdf)
            barrier.wait()  # Fails if files are processed serially.
            return document_result(count)
        class Converter:
            def submit(self, fn, source, target=None):
                future = Future()
                if target:
                    target.write_bytes(source.read_bytes())
                    future.set_result((2, []))
                else:
                    future.set_result(None)
                return future
        client = SimpleNamespace(close=lambda: None)
        with patch.object(jobs, 'CONVERTERS', Converter()), patch.object(jobs, 'parse_document', parse), patch.object(jobs, 'api_client', return_value=client):
            jobs.run_job(job)
        self.assertEqual(job['status'], 'completed')
        self.assertCountEqual(calls, [b'complete a', b'complete b'])
        for item in job['files']:
            self.assertEqual(item['api_calls'], 1)
            text = (jobs.folder(job) / item['id'] / 'result.md').read_text(encoding='utf-8')
            self.assertLess(text.index('Page 1 of 2 pages'), text.index('Page 2 of 2 pages'))
            self.assertIn('Whole-document overview', text)
            self.assertIn('Layout and reading order', text)

    def test_cancelled_batch_has_placeholder_for_every_file(self):
        job = self.import_items([('a.pdf', b'pdf'), ('b.txt', b'text')])
        job['cancel_requested'] = True
        with patch.object(jobs, 'api_client', return_value=SimpleNamespace(close=lambda: None)):
            jobs.run_job(job)
        self.assertEqual(job['status'], 'cancelled')
        self.assertTrue(all(f['has_output'] for f in job['files']))

    def test_retry_reconverts_legacy_pdf_but_reuses_current_conversion(self):
        job = self.import_items([('report.htm', b'<h1>ACTUAL_CONTENT</h1>')])
        item = job['files'][0]
        root = jobs.folder(job) / item['id']
        text_pdf('LEGACY_RAW_HTML', root / 'document.pdf')
        item.update(status='failed', page_count=1)
        conversions = []
        class Converter:
            def submit(self, fn, *args):
                conversions.append(fn)
                future = Future()
                future.set_result(fn(*args))
                return future
        with patch.object(jobs, 'CONVERTERS', Converter()), \
             patch.object(jobs, 'parse_document', side_effect=[ValueError('temporary API error'), document_result(1)]) as parse:
            jobs.run_file(job, item, object())
            self.assertEqual(item['status'], 'failed')
            self.assertEqual(item['conversion_version'], DOCUMENT_CONVERSION_VERSION)
            jobs.run_file(job, item, object())
        self.assertEqual(conversions.count(prepare_document), 1)
        self.assertIn(item['status'], jobs.DONE)
        for call in parse.call_args_list:
            with fitz.open(stream=call.args[1], filetype='pdf') as document:
                text = ''.join(page.get_text() for page in document)
                self.assertIn('ACTUAL_CONTENT', text)
                self.assertNotIn('LEGACY_RAW_HTML', text)

    def test_html_conversion_failure_does_not_send_old_pdf_to_model(self):
        job = self.import_items([('report.html', b'<img src="missing.png">')])
        item = job['files'][0]
        root = jobs.folder(job) / item['id']
        text_pdf('OLD_INCOMPLETE_CONTENT', root / 'document.pdf')
        item.update(status='failed', page_count=1)
        class Converter:
            def submit(self, fn, *args):
                future = Future()
                future.set_exception(ValueError('HTML 包含未載入的外部資源'))
                return future
        with patch.object(jobs, 'CONVERTERS', Converter()), patch.object(jobs, 'parse_document') as parse:
            jobs.run_file(job, item, object())
        self.assertEqual(item['status'], 'failed')
        self.assertNotIn('conversion_version', item)
        parse.assert_not_called()


class ConversionTests(unittest.TestCase):
    def test_existing_pdf_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'original.pdf', Path(temp) / 'document.pdf'
            text_pdf('ORIGINAL_PDF', source)
            self.assertEqual(convert_pdf(source, target), (1, []))
            self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_unbroken_urls_identifiers_and_chinese_keep_every_character(self):
        text = ('https://example.invalid/' + 'very-long-path-segment/' * 40 + '\n'
                + 'identifier_' * 200 + '\n' + '無空白長字串' * 200 + '\nFINAL_SENTINEL')
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'text.pdf'
            with patch.object(fitz.Page, 'get_text', side_effect=AssertionError('must not extract text')):
                text_pdf(text, target)
            with fitz.open(target) as document:
                rendered = ''.join(page.get_text() for page in document)
                self.assertEqual(''.join(text.split()), unicodedata.normalize('NFKC', ''.join(rendered.split())))

    def test_html_renders_svg_canvas_image_table_and_all_pages(self):
        picture = io.BytesIO()
        Image.new('RGB', (80, 80), '#0055ff').save(picture, format='PNG')
        data = base64.b64encode(picture.getvalue()).decode()
        markup = '''<!doctype html><style>
            body { font: 18px sans-serif; } section { break-before: page; }
            @media print { main { display: none; } }
            </style><div style="position:fixed;bottom:0">FIXED_ONCE</div><main>
            <h1>VISIBLE_TITLE</h1><svg width="240" height="120" viewBox="0 0 240 120">
            <rect width="240" height="80" fill="#ee1122"/>
            <text x="10" y="110">SVG_LABEL</text></svg>
            <img width="80" height="80" loading="lazy" src="data:image/png;base64,''' + data + '''">
            <canvas id="chart" width="200" height="80"></canvas>
            <script>const ctx = document.getElementById('chart').getContext('2d');
                ctx.fillStyle = '#00bb44'; ctx.fillRect(0, 0, 200, 80);</script>
            <table><tr><th>MEASURE</th><th>VALUE</th></tr><tr><td>OUTPUT</td><td>98765</td></tr></table>
            <section><details><summary>DETAILS</summary>HIDDEN_DETAIL</details>
            <div style="height:20px;overflow:auto"><div style="height:150px">SCROLL_START</div>SCROLL_END</div>
            <p>FINAL_SENTINEL</p></section></main>'''
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source.htm', Path(temp) / 'document.pdf'
            source.write_text(markup, encoding='utf-8')
            count, warnings = convert_pdf(source, target)
            self.assertEqual(count, 2)
            self.assertEqual(warnings, [])
            with fitz.open(target) as document:
                text = ''.join(page.get_text() for page in document)
                for marker in ('VISIBLE_TITLE', 'SVG_LABEL', 'MEASURE', '98765', 'HIDDEN_DETAIL', 'SCROLL_END', 'FINAL_SENTINEL'):
                    self.assertIn(marker, text)
                self.assertEqual(text.count('FIXED_ONCE'), 1)
                self.assertNotIn('fillRect', text)
                pixels = document[0].get_pixmap(alpha=False)
                colors = Image.frombytes('RGB', (pixels.width, pixels.height), pixels.samples).getcolors(pixels.width * pixels.height)
                for color in ((238, 17, 34), (0, 85, 255), (0, 187, 68)):
                    matching = sum(number for number, sample in colors
                                   if all(abs(a - b) <= 2 for a, b in zip(sample, color)))
                    self.assertGreater(matching, 500, color)

    def test_html_missing_resources_and_script_errors_fail(self):
        cases = ('<img src="https://example.invalid/image.png">',
                 '<link rel="stylesheet" href="https://example.invalid/site.css">',
                 '<script src="https://example.invalid/chart.js"></script>',
                 '<img src="file:///unavailable-private-file.png">',
                 '<img src="data:image/png;base64,AAAA">',
                 '<script>throw new Error("incomplete chart")</script>')
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source.html', Path(temp) / 'document.pdf'
            for markup in cases:
                with self.subTest(markup=markup):
                    source.write_text(markup, encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'HTML'):
                        convert_pdf(source, target)
                    self.assertFalse(target.exists())

    def test_all_pages_render_without_text_extraction_and_previews_are_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'document.pdf'
            with fitz.open() as pdf:
                for label in ('FIRST_PAGE', 'LAST_PAGE'):
                    page = pdf.new_page()
                    page.insert_text((30, 40), label)
                    page.insert_text((300, 40), 'SEPARATE_NODE')
                pdf.save(target)
            manifest = target.parent / 'page-previews.json'
            manifest.write_text('{"version": 0}', encoding='utf-8')
            with patch.object(fitz.Page, 'get_text', side_effect=AssertionError('must not extract text')):
                prepare_page_previews(target)
            self.assertEqual(json.loads(manifest.read_text(encoding='utf-8'))['page_count'], 2)
            self.assertTrue((target.parent / 'pages/2.png').exists())
            with Image.open(target.parent / 'pages/1.png') as picture:
                self.assertGreater(max(picture.size), 2000)
            with patch('backend.converters.fitz.open', side_effect=AssertionError('must use cache')):
                prepare_page_previews(target)
            (target.parent / 'pages/2.png').unlink()
            prepare_page_previews(target)
            self.assertTrue((target.parent / 'pages/2.png').exists())

    def test_long_chinese_text_is_paginated_without_losing_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'text.pdf'
            text_pdf('\n'.join(f'Page {n} 行內容' for n in range(200)) + '\nFINAL_SENTINEL', target)
            with fitz.open(target) as document:
                self.assertGreater(len(document), 1)
                self.assertIn('FINAL_SENTINEL', ''.join(p.get_text() for p in document))

    def test_multiframe_image_keeps_all_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'image.tiff', Path(temp) / 'document.pdf'
            frames = [Image.new('RGB', (64, 64), color) for color in ['red', 'blue']]
            frames[0].save(source, save_all=True, append_images=frames[1:])
            count, _ = convert_pdf(source, target)
            self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
