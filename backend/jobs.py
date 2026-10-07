import copy
import hashlib
import json
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import config, drive
from .converters import prepare_document, prepare_page_previews, prepare_audio, DOCUMENT_CONVERSION_VERSION
from .models import api_client, parse_document, transcribe, summarize_transcript, safe_error, REPORT_VERSION

LOCK = threading.RLock()
API_SLOTS = threading.BoundedSemaphore(6)
JOBS = {}
RUNNERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='batch')
CONVERTERS = None
ACTIVE = {'queued', 'running', 'cancelling'}
DONE = {'completed', 'review'}


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(job):
    return config.DATA / job['id']


def save(job):
    # All mutations and reads use LOCK, and persistence is an atomic file replacement.
    with LOCK:
        job['updated_at'] = now()
        target = folder(job) / 'job.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(target)


def snapshot(job):
    with LOCK:
        result = copy.deepcopy(job)
    for item in result['files']:
        item['completed_pages'] = sum(p['status'] in DONE for p in item['pages'])
        item['review_pages'] = sum(p['status'] == 'review' for p in item['pages'])
    return result


def startup():
    global CONVERTERS
    config.DATA.mkdir(parents=True, exist_ok=True)
    CONVERTERS = ProcessPoolExecutor(max_workers=2)
    for path in config.DATA.glob('*/job.json'):
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            if job['status'] in ACTIVE:
                job['status'] = 'interrupted'
                job['cancel_requested'] = False
                for item in job['files']:
                    if item['status'] in ACTIVE | {'downloading', 'converting', 'parsing', 'auditing', 'summarizing'}:
                        item['status'] = 'interrupted'
                save(job)
            JOBS[job['id']] = job
        except (ValueError, KeyError):
            continue


def shutdown():
    with LOCK:
        for job in JOBS.values():
            if job['status'] in ACTIVE:
                job['cancel_requested'] = True
                save(job)
    RUNNERS.shutdown(wait=True, cancel_futures=True)
    if CONVERTERS:
        CONVERTERS.shutdown(wait=True, cancel_futures=True)


def import_drive(scan):
    job_id = uuid.uuid4().hex
    root = config.DATA / job_id
    root.mkdir(parents=True)
    files = []
    for entry in scan['files']:
        item_id = f'{len(files) + 1:04}'
        (root / item_id).mkdir()
        files.append({**entry, 'id': item_id, 'sha256': '', 'source_downloaded': False,
                      'kind': config.file_kind(entry['name']) if not entry['source_error'] else 'unsupported',
                      'status': 'ready', 'stage': 'Waiting to download', 'pages': [], 'page_count': 0,
                      'warnings': [], 'error': None, 'has_output': False})
    job = {'id': job_id, 'name': scan['folder_name'], 'created_at': now(), 'updated_at': now(),
           'status': 'ready', 'cancel_requested': False, 'concurrency': 4,
           'document_model': config.DOCUMENT_MODEL, 'transcription_model': config.TRANSCRIPTION_MODEL,
           'files': files, 'ignored': [], 'source': {
               'type': 'google_drive', 'folder_id': scan['folder_id'],
               'url': f'https://drive.google.com/drive/folders/{scan["folder_id"]}',
               'account': scan['account'], 'recursive': scan['recursive'],
               'excluded_folders': scan['excluded_folders']}}
    with LOCK:
        JOBS[job_id] = job
        save(job)
    from . import knowledge
    knowledge.register_library(job)
    return snapshot(job)


def update(job, target, **values):
    with LOCK:
        target.update(values)
        save(job)


def cancelled(job):
    with LOCK:
        return job['cancel_requested']


def write_markdown(job, item):
    root = folder(job) / item['id']
    name = item['name'].replace('\n', ' ')
    media = item['kind'] in {'audio', 'video'}
    model = f'{job["transcription_model"]} → {config.REPORT_MODEL}' if media else job['document_model']
    text = [f'# {name}', '', f'> Source: {name}', f'> Model: {model}',
            f'> Status: {item["status"]}; original SHA-256: {item["sha256"]}', '',
            '> Automated transcription and model checks do not replace human review. Inspect uncertainty markers and the manifest.', '']
    if 'drive' in item:
        text.extend([f'> Drive source: https://drive.google.com/open?id={item["drive"]["id"]}', ''])
    for warning in item['warnings']:
        text.extend([f'> Note: {warning}', ''])
    if item['error']:
        text.extend([f'> Processing error: {item["error"]}', ''])
    if item.get('integration'):
        text.extend(['## Complete report' if media else '## Whole-document overview', '', item['integration'], ''])
    for page in ([] if media and item.get('report_ready') else sorted(item['pages'], key=lambda p: p['number'])):
        n = page['number']
        heading = f'Page {n} of {item["page_count"]} pages'
        if item['kind'] in {'audio', 'video'}:
            heading = 'Full audio transcript'
        text.extend(['---', '', f'## {heading}', f'<!-- source-unit: {n}; status: {page["status"]} -->', ''])
        result_path = root / 'pages' / f'{n}.json'
        if page['status'] in DONE and result_path.exists():
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('purpose'):
                text.extend(['### Page purpose and context', '', result['purpose'], ''])
            if result.get('layout'):
                text.extend(['### Layout and reading order', '', result['layout'], ''])
            text.extend(['### Complete content', ''])
            text.extend([result['markdown'], ''])
            for issue in result['issues']:
                text.extend([f'> Review needed: {issue}', ''])
        else:
            text.extend([f'> [Transcription incomplete] {page.get("error") or "Cancelled or not processed. Do not treat this as a blank page."}', ''])
    (root / 'result.md').write_text('\n'.join(text), encoding='utf-8')
    update(job, item, has_output=True)


def ensure_report(job, item, client):
    """Upgrade completed legacy transcriptions, or resume after only the report stage failed."""
    root = folder(job) / item['id']
    transcript_path = root / 'transcript.md'
    if not transcript_path.exists():
        page = root / 'pages' / '1.json'
        if not page.exists():
            raise ValueError('Process the media file to create its transcript first.')
        transcript_path.write_text(json.loads(page.read_text(encoding='utf-8'))['markdown'], encoding='utf-8')
    report_path = root / 'report.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    if report.get('version') != REPORT_VERSION:
        update(job, item, stage='Writing the complete report')
        with API_SLOTS:
            update(job, item, api_calls=item.get('api_calls', 0) + 1)
            report = summarize_transcript(client, transcript_path.read_text(encoding='utf-8'), item['name'],
                                          usage_scope={'job_id': job['id'], 'file_id': item['id'], 'label': item['name']})
        report['version'] = REPORT_VERSION
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'report.md').write_text(report['text'], encoding='utf-8')
    update(job, item, integration=report['text'], report_ready=True, report_model=report['model'])
    return report['text']


def run_file(job, item, client, download_budget=None):
    if cancelled(job):
        return
    root = folder(job) / item['id']
    source = root / ('source' + Path(item['name']).suffix.lower())
    media = item['kind'] in {'audio', 'video'}
    try:
        if item.get('source_error'):
            raise ValueError(item['source_error'])
        if item['kind'] == 'unsupported':
            raise ValueError('Unsupported format. Convert this file to PDF and import it again from Drive or ZIP. The file remains listed and was not silently skipped.')
        if 'drive' in item and not (source.exists() and item.get('source_downloaded')):
            update(job, item, status='downloading', stage='Downloading the complete file from Google Drive', error=None)
            drive_client = drive.DriveClient()
            try:
                if drive_client.identity['email'] != job['source']['account']:
                    raise ValueError('The service account changed. Read the folder again to create a new batch.')
                downloaded = drive_client.download(item, source, download_budget, lambda: cancelled(job))
                update(job, item, **downloaded, source_downloaded=True)
            finally:
                drive_client.close()
        if cancelled(job):
            update(job, item, status='cancelled', stage='Cancelled')
            return
        update(job, item, status='converting', stage='Preparing the full audio track' if media else 'Converting the full document to PDF', error=None)
        if media:
            audio, duration = prepare_audio(source, root)
            warnings = ['The full audio track was transcribed in one request, without word timestamps or speaker labels.']
            if item['kind'] == 'video':
                warnings.append('Video processing transcribes audio only. Frames, subtitles, and slides were not analyzed.')
            update(job, item, pages=[{'number': 1, 'status': 'ready', 'issues': [], 'start': 0, 'end': duration}],
                   page_count=1, duration=duration, warnings=warnings)
        else:
            if (not (root / 'document.pdf').exists() or not item['page_count']
                    or item.get('conversion_version') != DOCUMENT_CONVERSION_VERSION):
                count, warnings = CONVERTERS.submit(prepare_document, source, root / 'document.pdf').result()
                update(job, item, page_count=count, warnings=warnings, conversion_version=DOCUMENT_CONVERSION_VERSION)
            CONVERTERS.submit(prepare_page_previews, root / 'document.pdf').result()
            update(job, item, pages=[{'number': n + 1, 'status': 'ready', 'issues': []}
                                    for n in range(item['page_count'])])
        with API_SLOTS:
            if cancelled(job):
                update(job, item, status='cancelled', stage='Cancelled')
                return
            with LOCK:
                for page in item['pages']:
                    page['status'] = 'parsing'
                update(job, item, status='parsing', stage='Transcribing the full audio track' if media else 'Parsing the complete PDF in one request')
            if media:
                transcript_path = root / 'transcript.md'
                if transcript_path.exists():
                    transcript = transcript_path.read_text(encoding='utf-8')
                else:
                    update(job, item, api_calls=item.get('api_calls', 0) + 1)
                    transcript = transcribe(client, audio, duration=duration,
                                            usage_scope={'job_id': job['id'], 'file_id': item['id'], 'label': item['name']})
                    transcript_path.write_text(transcript, encoding='utf-8')
                result = {'page_count': 1, 'integration': '',
                          'pages': [{'number': 1, 'markdown': transcript,
                                     'issues': [], 'start': 0, 'end': duration}]}
            else:
                update(job, item, api_calls=item.get('api_calls', 0) + 1)
                result = parse_document(client, (root / 'document.pdf').read_bytes(), item['page_count'],
                                        usage_scope={'job_id': job['id'], 'file_id': item['id'], 'label': item['name']})
        # The single whole-document response is indexed locally for browsing; no extra API requests.
        (root / 'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        pages_dir = root / 'pages'
        pages_dir.mkdir(exist_ok=True)
        statuses = []
        for data in result['pages']:
            issues = list(data['issues'])
            for key, passed in data.get('checks', {}).items():
                if not passed:
                    issues.append(f'Content check not confirmed: {key}')
            data['issues'] = issues
            (pages_dir / f'{data["number"]}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            statuses.append({'number': data['number'], 'status': 'review' if issues else 'completed',
                             'issues': issues, **({'start': 0, 'end': duration} if media else {})})
        if media:
            update(job, item, status='summarizing', stage='Writing the complete report', pages=statuses)
            result['integration'] = ensure_report(job, item, client)
        status = 'review' if item['warnings'] or any(p['status'] == 'review' for p in statuses) else 'completed'
        update(job, item, pages=statuses, integration=result['integration'], status=status, stage='Parsing and page checks complete')
    except Exception as error:
        with LOCK:
            for page in item['pages']:
                if media and page['status'] in DONE and (root / 'transcript.md').exists():
                    continue
                page['status'] = 'failed'
                page['error'] = safe_error(error)
        update(job, item, status='failed', error=safe_error(error), stage='Processing failed')
    finally:
        write_markdown(job, item)


def run_job(job):
    try:
        update(job, job, status='running')
        client = api_client()
        download_budget = drive.DownloadBudget(sum(f['size'] for f in job['files'] if f.get('source_downloaded') and (folder(job) / f['id'] / ('source' + Path(f['name']).suffix.lower())).exists()))
        try:
            # Files run concurrently; media adds a report request after the intact transcription.
            with ThreadPoolExecutor(max_workers=job['concurrency'], thread_name_prefix='file') as executor:
                futures = [executor.submit(run_file, job, item, client, download_budget) for item in job['files'] if item['status'] not in DONE]
                for future in as_completed(futures):
                    future.result()
        finally:
            client.close()
        if cancelled(job):
            status = 'cancelled'
        elif any(f['status'] == 'failed' for f in job['files']):
            status = 'partial'
        elif any(f['status'] == 'review' for f in job['files']):
            status = 'review'
        else:
            status = 'completed'
        # Ensure every source file has a corresponding Markdown, including cancelled/unsupported files.
        for item in job['files']:
            if item['status'] not in DONE | {'failed'}:
                update(job, item, status='cancelled', stage='Cancelled')
            write_markdown(job, item)
        update(job, job, status=status)
        if job.get('build_index') and not cancelled(job):
            from . import knowledge
            knowledge.start_build(job['id'])
    except Exception as error:
        update(job, job, status='failed', error=safe_error(error))


def start_job(job, concurrency):
    with LOCK:
        if job['status'] in ACTIVE:
            raise ValueError('This batch is already processing.')
        if job['status'] in DONE:
            raise ValueError('This batch is complete. Compare items needing review with their sources.')
        job.update(status='queued', cancel_requested=False, concurrency=concurrency, build_index=True)
        save(job)
        RUNNERS.submit(run_job, job)
    return snapshot(job)


def output_text(job, item, variant=None):
    from . import knowledge
    if item.get('knowledge_id'):
        doc = knowledge.get_document(item['knowledge_id'], library_id=job['id'])
        role = variant or ('report' if doc['media'] else 'parsed')
        asset = doc.get('assets', {}).get(role, {})
        if asset.get('provider') == 'gcs' and asset.get('status') == 'uploaded':
            return knowledge.read_markdown(doc, role)
    name = {'transcript': 'transcript.md', 'report': 'report.md'}.get(variant, 'result.md')
    path = folder(job) / item['id'] / name
    return path.read_text(encoding='utf-8') if path.exists() else ''


def make_archive(job):
    root = folder(job)
    output = root / 'markdown.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in job['files']:
            text = output_text(job, item)
            if text:
                archive.writestr('markdown/' + item['name'] + '.md', text.encode('utf-8'))
            response = root / item['id'] / 'response.json'
            if response.exists():
                archive.write(response, f'audit/{item["id"]}/response.json')
            if item['kind'] in {'audio', 'video'}:
                for role in ('transcript', 'report'):
                    text = output_text(job, item, role)
                    if text:
                        archive.writestr(f'text/{item["name"]}.{role}.md', text.encode('utf-8'))
            path = root / item['id'] / 'report.json'
            if path.exists():
                archive.write(path, f'text/{item["name"]}.report.json')
        archive.writestr('manifest.json', json.dumps(snapshot(job), ensure_ascii=False, indent=2))
        archive.writestr('README.md', '# Folio outputs\n\nmarkdown/ contains a Markdown file per source. Media uses a complete report.\n\ntext/ contains transcripts, reports, and report metadata. manifest.json records every source, page, status, and error. audit/ contains complete parsing responses.\n\nIncomplete files and review markers require manual verification.\n')
    return output
