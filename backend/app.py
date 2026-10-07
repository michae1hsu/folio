import json
import asyncio
import tempfile
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, jobs, drive, knowledge, chat, gcs, usage, evaluation
from .models import api_client, openai_key_configured, safe_error
from . import zip_import
from starlette.concurrency import run_in_threadpool


@asynccontextmanager
async def lifespan(app):
    jobs.startup()
    migrated = knowledge.startup()
    chat.startup()
    usage.startup(jobs.JOBS, knowledge.STATE['documents'], chat.CHATS)
    evaluation.startup()
    if migrated:
        for library in knowledge.status()['libraries']:
            knowledge.start_build(library['id'])
    yield
    jobs.shutdown()
    knowledge.shutdown()
    chat.shutdown()
    evaluation.shutdown()


app = FastAPI(title='Folio', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_write_guard(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'DELETE', 'PATCH'} and request.headers.get('x-folio-request') != '1':
        return JSONResponse({'detail': 'Use the Folio workbench to perform this operation.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def get_job(job_id):
    with jobs.LOCK:
        job = jobs.JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, 'Batch not found.')
        return job


def get_item(job_id, item_id):
    job = get_job(job_id)
    item = next((item for item in job['files'] if item['id'] == item_id), None)
    if item is None:
        raise HTTPException(404, 'File not found.')
    return job, item


@app.get('/api/health')
def health():
    return {'key_configured': openai_key_configured(),
            'document_model': config.DOCUMENT_MODEL, 'transcription_model': config.TRANSCRIPTION_MODEL,
            'libreoffice': bool(shutil.which('soffice') or shutil.which('libreoffice')),
            'ffmpeg': bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),
            'api_concurrency': 6, 'requests_per_file': 1, 'media_requests_per_file': 2,
            'report_model': config.REPORT_MODEL, 'agent_model': config.AGENT_MODEL, 'max_files': config.MAX_FILES,
            'supported': sorted(config.AUDIO | config.VIDEO | config.IMAGES | config.OFFICE | config.TEXT | {'.pdf'})}


@app.get('/api/jobs')
def list_jobs():
    with jobs.LOCK:
        return [jobs.snapshot(j) for j in sorted(jobs.JOBS.values(), key=lambda j: j['created_at'], reverse=True)]


@app.get('/api/usage')
def read_usage(job_id: str | None = None, file_id: str | None = None,
               chat_id: str | None = None, turn_id: str | None = None):
    if file_id and not job_id or turn_id and not chat_id or job_id and chat_id:
        raise HTTPException(400, 'Choose one batch or conversation scope.')
    if job_id:
        get_item(job_id, file_id) if file_id else get_job(job_id)
    if chat_id:
        read_chat(chat_id)
    return usage.report(job_id=job_id, file_id=file_id, chat_id=chat_id, turn_id=turn_id)


@app.get('/api/jobs/{job_id}')
def read_job(job_id: str):
    return jobs.snapshot(get_job(job_id))


@app.get('/api/drive/status')
def drive_status():
    return drive.status()


class DriveSettings(BaseModel):
    credentials_path: str = Field(min_length=1, max_length=4096)


@app.post('/api/drive/configure')
def configure_drive(request: DriveSettings):
    try:
        return drive.configure(request.credentials_path)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post('/api/drive/test')
def test_drive():
    try:
        client = drive.DriveClient()
        try:
            with client.get('/about', params={'fields': 'user(emailAddress)'}) as response:
                response.json()
            return {**drive.status(), 'authenticated': True, 'message': 'Google Drive connected. You can read folders shared with the service account.'}
        finally:
            client.close()
    except ValueError as error:
        raise HTTPException(400, str(error))


class DriveImport(BaseModel):
    folder: str = Field(min_length=1, max_length=2048)
    recursive: bool = True


@app.post('/api/drive/import')
def import_drive(request: DriveImport):
    try:
        client = drive.DriveClient()
        try:
            scan = client.scan(request.folder, request.recursive)
            return jobs.import_drive(scan)
        finally:
            client.close()
    except ValueError as error:
        raise HTTPException(400, str(error))


class StartRequest(BaseModel):
    concurrency: int = Field(default=4, ge=2, le=6)


@app.post('/api/zip/import')
async def import_zip(request: Request, filename: str = 'upload.zip'):
    if request.headers.get('content-type', '').split(';')[0] not in {'application/zip', 'application/octet-stream'}:
        raise HTTPException(415, 'Send the ZIP file as an application/zip request body.')
    if len(filename) > 255 or Path(filename).suffix.lower() != '.zip':
        raise HTTPException(400, 'Choose a .zip file with a name of at most 255 characters.')
    config.DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.upload-', dir=config.DATA) as temporary:
        path = Path(temporary) / 'upload.zip'
        size = 0
        with path.open('wb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > config.MAX_UPLOAD:
                    raise HTTPException(413, 'ZIP upload exceeds the 512 MiB limit.')
                await run_in_threadpool(target.write, chunk)
        try:
            return await run_in_threadpool(zip_import.import_zip, path, filename)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None


@app.post('/api/jobs/{job_id}/start')
def start(job_id: str, request: StartRequest):
    if not openai_key_configured():
        raise HTTPException(400, 'Configure OPENAI_API_KEY or FOLIO_OPENAI_DIRECT_KEY on the backend.')
    try:
        return jobs.start_job(get_job(job_id), request.concurrency)
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    job = get_job(job_id)
    with jobs.LOCK:
        if job['status'] not in jobs.ACTIVE:
            raise HTTPException(409, 'This batch has no active work.')
        jobs.update(job, job, cancel_requested=True, status='cancelling')
    return jobs.snapshot(job)


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str):
    job = get_job(job_id)
    with jobs.LOCK:
        if job['status'] in jobs.ACTIVE | {'ready'}:
            raise HTTPException(409, 'Wait for the batch to finish before downloading.')
        archive = jobs.make_archive(job)
    return FileResponse(archive, media_type='application/zip', filename=f'{Path(job["name"]).stem}-markdown.zip')


@app.get('/api/jobs/{job_id}/files/{item_id}/markdown')
def markdown(job_id: str, item_id: str):
    job, item = get_item(job_id, item_id)
    try:
        return {'text': jobs.output_text(job, item), 'name': item['name'] + '.md'}
    except ValueError as error:
        raise HTTPException(502, str(error))


@app.get('/api/jobs/{job_id}/files/{item_id}/pdf')
def pdf(job_id: str, item_id: str):
    job, _ = get_item(job_id, item_id)
    path = jobs.folder(job) / item_id / 'document.pdf'
    if not path.exists():
        raise HTTPException(404, 'The PDF is not ready yet.')
    return FileResponse(path, media_type='application/pdf', filename='document.pdf', content_disposition_type='inline')


@app.get('/api/jobs/{job_id}/files/{item_id}/pages/{page_number}')
def page_result(job_id: str, item_id: str, page_number: int):
    job, item = get_item(job_id, item_id)
    if page_number < 1 or page_number > item['page_count']:
        raise HTTPException(404, 'Page not found.')
    path = jobs.folder(job) / item_id / 'pages' / f'{page_number}.json'
    if not path.exists():
        return {'number': page_number, 'markdown': '', 'issues': [], 'pending': True}
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/api/jobs/{job_id}/files/{item_id}/pages/{page_number}/image')
def page_image(job_id: str, item_id: str, page_number: int):
    job, _ = get_item(job_id, item_id)
    path = jobs.folder(job) / item_id / 'pages' / f'{page_number}.png'
    if not path.exists():
        raise HTTPException(404, 'The page preview is not ready yet.')
    return FileResponse(path, media_type='image/png')


@app.get('/api/knowledge')
def knowledge_status(library_id: str | None = None):
    try:
        return knowledge.status(library_id)
    except ValueError as error:
        raise HTTPException(404, str(error))


@app.post('/api/knowledge/test')
def test_knowledge(library_id: str):
    return knowledge.connection(library_id)


class SearchRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=100, ge=1, le=500, strict=True)
    judge_provider: Literal['jev', 'openai_decisions'] = 'jev'


@app.post('/api/search')
def search_knowledge(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(400, 'Enter a search query.')
    try:
        return {'mode': 'search', **knowledge.search_files(query, request.top_k, question=query,
                                                         library_id=request.library_id, judge_provider=request.judge_provider)}
    except ValueError as error:
        raise HTTPException(400, str(error))
    except Exception as error:
        raise HTTPException(502, safe_error(error))


class BuildRequest(BaseModel):
    job_id: str


@app.post('/api/knowledge/build')
def build_knowledge(request: BuildRequest):
    get_job(request.job_id)
    try:
        return knowledge.start_build(request.job_id)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get('/api/knowledge/{document_id}/markdown')
def knowledge_markdown(document_id: str, library_id: str, variant: str = 'parsed'):
    try:
        doc = knowledge.get_document(document_id, library_id)
        if variant not in doc.get('assets', {}):
            raise ValueError('This Markdown variant is unavailable.')
        return {'text': knowledge.read_markdown(doc, variant), 'name': doc['name'] + f'.{variant}.md'}
    except ValueError as error:
        raise HTTPException(404, str(error))


@app.get('/api/knowledge/{document_id}/links')
def knowledge_links(document_id: str, library_id: str):
    try:
        return {'links': knowledge.public_document(knowledge.get_document(document_id, library_id))['links']}
    except ValueError as error:
        raise HTTPException(404, str(error))


@app.get('/api/knowledge/{document_id}/original')
def original_upload(document_id: str, library_id: str):
    try:
        doc = knowledge.get_document(document_id, library_id)
        content = zip_import.read_original(doc)
        filename = quote(Path(doc['name']).name, safe='')
        return Response(content, media_type='application/octet-stream',
                        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


class StorageSettings(BaseModel):
    bucket: str = Field(min_length=3, max_length=230)


@app.post('/api/storage/settings')
def storage_settings(request: StorageSettings):
    try:
        return gcs.configure(request.bucket)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post('/api/storage/test')
def test_storage():
    try:
        return gcs.connection()
    except Exception as error:
        return {'connected': False, 'message': gcs.error_message(error)}


@app.get('/api/jobs/{job_id}/files/{item_id}/transcript')
def transcript_markdown(job_id: str, item_id: str):
    job, item = get_item(job_id, item_id)
    try:
        return {'text': jobs.output_text(job, item, 'transcript'), 'name': item['name'] + '.transcript.md'}
    except ValueError as error:
        raise HTTPException(502, str(error))


@app.get('/api/agent/models')
def agent_models():
    if not openai_key_configured():
        return {'models': [config.AGENT_MODEL], 'default': config.AGENT_MODEL, 'verified': False,
                'error': 'Configure OPENAI_API_KEY in the backend .env and restart Folio to use document chat.'}
    try:
        with api_client() as client:
            ids = sorted(m.id for m in client.models.list() if m.id.startswith(('gpt-6', 'gpt-5.6')) and not any(t in m.id for t in ('image', 'audio', 'realtime', 'transcribe')))
        return {'models': ids, 'default': config.AGENT_MODEL, 'verified': True}
    except Exception as error:
        return {'models': [config.AGENT_MODEL], 'default': config.AGENT_MODEL, 'verified': False, 'error': safe_error(error)}


@app.get('/api/chats')
def chat_list():
    return chat.list_chats()


class ChatRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=100)
    model: str = Field(default=config.AGENT_MODEL, min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._-]+$')
    top_k: int = Field(default=100, ge=1, le=500)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    judge_provider: Literal['jev', 'openai_decisions'] = 'jev'


@app.post('/api/chats')
def create_chat(request: ChatRequest):
    try:
        return chat.create(request.library_id, request.model, request.top_k, request.system_prompt, request.judge_provider)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get('/api/chats/{chat_id}')
def read_chat(chat_id: str):
    try:
        return chat.snapshot(chat_id)
    except ValueError as error:
        raise HTTPException(404, str(error))


class PromptRequest(BaseModel):
    system_prompt: str = Field(min_length=1, max_length=20000)


class ChatSettingsRequest(PromptRequest):
    top_k: int = Field(default=100, ge=1, le=500, strict=True)
    expected_revision: int | None = Field(default=None, ge=1)
    judge_provider: Literal['jev', 'openai_decisions'] | None = None


@app.get('/api/agent/settings')
def agent_settings():
    return chat.agent_defaults()


@app.patch('/api/agent/settings')
def update_agent_defaults(request: PromptRequest):
    try:
        return chat.set_default_prompt(request.system_prompt)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.patch('/api/chats/{chat_id}/settings')
def update_chat_settings(chat_id: str, request: ChatSettingsRequest):
    read_chat(chat_id)
    try:
        return chat.update_settings(chat_id, **request.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error))


class EvaluationSettingsRequest(BaseModel):
    enabled: bool = Field(strict=True)
    threshold: float = Field(ge=0, le=3, allow_inf_nan=False)
    auto_optimize: bool = Field(strict=True)
    judge_provider: Literal['jev', 'openai_decisions'] | None = None


@app.get('/api/evaluation-settings')
def evaluation_settings():
    return evaluation.settings()


@app.patch('/api/evaluation-settings')
def update_evaluation_settings(request: EvaluationSettingsRequest):
    try:
        return evaluation.configure(request.model_dump(exclude_none=True))
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get('/api/evaluations')
def evaluations(library_id: str | None = None, chat_id: str | None = None, status: str | None = None, offset: int = 0, limit: int = 50):
    if not 0 <= offset or not 1 <= limit <= 100:
        raise HTTPException(400, 'Invalid pagination range.')
    if library_id:
        try:
            knowledge.get_library(library_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
    if chat_id:
        value = read_chat(chat_id)
        if library_id and value['library_id'] != library_id:
            raise HTTPException(400, 'The conversation does not belong to the selected library.')
    return evaluation.history(library_id=library_id, chat_id=chat_id, status=status, offset=offset, limit=limit)


@app.get('/api/evaluations/{record_id}')
def evaluation_detail(record_id: str):
    try:
        return evaluation.detail(record_id)
    except ValueError as error:
        raise HTTPException(404, str(error))


class TurnEvaluationRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=200)


@app.post('/api/chats/{chat_id}/evaluations')
def evaluate_past_turn(chat_id: str, request: TurnEvaluationRequest):
    read_chat(chat_id)
    with chat.LOCK:
        value = chat.CHATS[chat_id]
        try:
            chat.require_library(value)
        except ValueError as error:
            raise HTTPException(409, str(error))
        if value.get('response_active') or value['status'] in {'running', 'queued', 'disconnected', 'cancelling'}:
            raise HTTPException(409, 'Wait for the conversation to finish or reconnect before evaluating past answers.')
        if not any(m.get('turn_id') == request.turn_id and m.get('phase') == 'final_answer' for m in value['messages']):
            raise HTTPException(404, 'No completed answer found for this turn.')
        return evaluation.capture(value, request.turn_id, automatic=False)


@app.post('/api/evaluations/{record_id}/retry')
def retry_evaluation(record_id: str):
    try:
        evaluation.get(record_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    try:
        return evaluation.retry(record_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.post('/api/evaluations/{record_id}/apply-prompt')
def apply_evaluation_prompt(record_id: str):
    try:
        record = evaluation.get(record_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    proposed = record.get('optimization', {}).get('proposed_system_prompt')
    if record.get('example'):
        raise HTTPException(409, 'This record has no editable conversation. Copy the suggestion to your own conversation settings.')
    if record['optimization']['status'] != 'completed' or not proposed or record['prompt_revision'] is None:
        raise HTTPException(409, 'This record has no applicable prompt suggestion or saved revision.')
    current = read_chat(record['chat_id'])
    try:
        return chat.update_settings(record['chat_id'], system_prompt=proposed, top_k=current['top_k'],
                                    expected_revision=record['prompt_revision'])
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.post('/api/chats/{chat_id}/usage/refresh')
def refresh_chat_usage(chat_id: str):
    read_chat(chat_id)
    try:
        return chat.refresh_usage(chat_id)
    except Exception as error:
        raise HTTPException(502, 'Agent usage could not be refreshed. Saved records are still available. ' + safe_error(error))


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)


@app.post('/api/chats/{chat_id}/messages')
def send_message(chat_id: str, request: MessageRequest):
    read_chat(chat_id)
    if not request.text.strip():
        raise HTTPException(400, 'Enter a question.')
    try:
        return chat.send(chat_id, request.text.strip())
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.post('/api/chats/{chat_id}/resume')
def resume_chat(chat_id: str):
    read_chat(chat_id)
    try:
        return chat.resume(chat_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.post('/api/chats/{chat_id}/cancel')
def cancel_chat(chat_id: str):
    read_chat(chat_id)
    try:
        return chat.cancel(chat_id)
    except Exception as error:
        raise HTTPException(400, safe_error(error))


@app.get('/api/chats/{chat_id}/events')
async def chat_events(chat_id: str, request: Request):
    read_chat(chat_id)
    async def events():
        revision = -1
        heartbeat = 0
        while not await request.is_disconnected():
            value = chat.snapshot(chat_id)
            if value['revision'] != revision:
                revision = value['revision']
                yield f'id: {revision}\nevent: state\ndata: {json.dumps(value, ensure_ascii=False)}\n\n'
            elif heartbeat % 30 == 0:
                yield ': heartbeat\n\n'
            heartbeat += 1
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


if (config.ROOT / 'dist').exists():
    app.mount('/', StaticFiles(directory=config.ROOT / 'dist', html=True), name='frontend')
