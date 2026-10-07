"""Durable API usage ledger. Counts are reported; USD amounts are price estimates."""
import copy
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from . import config

PRICE_DATE = '2026-09-30'
OPENAI_PRICE = 'https://developers.openai.com/api/docs/pricing'
JEV_PRICE = 'https://typesafe.ai/blog/introducing-system-one-models-and-jev'
DECISIONS_PRICE = 'https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability'
# USD per million tokens: ordinary input, cached input, cache write, output.
RATES = {
    'gpt-6.1-sol': (2, .10, 2.5, 10),
    'gpt-6-sol': (2, .20, 2.5, 10),
    'gpt-6-luna': (.10, .01, .125, .50),
    'gpt-6-astra': (10, 1, 12.5, 50),
    'gpt-5.6-sol': (4, .40, 5, 20),
    'gpt-5.6-terra': (2, .20, 2.5, 12),
    'gpt-5.6-luna': (.20, .02, .25, 1.20),
}
LABELS = {'document': 'Document transcription', 'transcription': 'Audio transcription', 'report': 'Media report',
          'index_embedding': 'Index embeddings', 'query_embedding': 'Query embeddings',
          'jev': 'JEV relevance scoring', 'openai_decisions': 'OpenAI Decisions relevance scoring', 'agent': 'Agent conversation',
          'answer_eval': 'Answer evaluation', 'optimization': 'Agent suggestions'}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    config.DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DATA / 'usage.sqlite3', timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, job_id TEXT, file_id TEXT, chat_id TEXT, turn_id TEXT, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        yield db
        db.commit()
    finally:
        db.close()


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def raw_usage(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def calculate(entry):
    raw = entry.get('usage') or {}
    inp = number(raw.get('input_tokens', raw.get('prompt_tokens')))
    out = number(raw.get('output_tokens', raw.get('completion_tokens')))
    if entry['operation'] in {'index_embedding', 'query_embedding'} and inp is not None:
        out = 0  # Embedding creates vectors, not output tokens.
    details = raw.get('input_tokens_details') or {}
    cached = number(details.get('cached_tokens'))
    writes = number(details.get('cache_write_tokens'))
    reasoning = number((raw.get('output_tokens_details') or {}).get('reasoning_tokens'))
    total = number(raw.get('total_tokens'))
    if total is None and inp is not None and out is not None:
        total = inp + out
    entry.update(input_tokens=inp, output_tokens=out, cached_tokens=cached, cache_write_tokens=writes,
                 reasoning_tokens=reasoning, total_tokens=total, cost_usd=None,
                 price_date=PRICE_DATE, price_source=OPENAI_PRICE, price_note='', rates=None)
    model = entry['model']
    if entry.get('provider') == 'openai_decisions' or entry['operation'] == 'openai_decisions':
        entry.update(price_date='2026-10-07', price_source=DECISIONS_PRICE)
        if model != 'gpt-6-luna' or inp is None:
            entry['price_note'] = 'Decisions model price or input usage is unconfirmed; cost is unknown.'
            return entry
        cached, writes = cached or 0, writes or 0
        if cached + writes > inp:
            entry['price_note'] = 'Usage details do not match the input total; cost needs review.'
            return entry
        rate = .2 if inp > 272000 else .1
        entry.update(cost_usd=float(Decimal(str(inp - cached - writes)) * Decimal(str(rate)) / 1_000_000),
                     rates={'input': rate, 'cached': 0, 'cache_write': 0, 'output': 0},
                     price_note='Decisions input-only pricing; cache reads, cache writes, and output are free. Regional processing premiums are excluded.'
                                + (' Input exceeds 272K; the long-context rate applies.' if inp > 272000 else ''))
        return entry
    if model == 'gpt-transcribe':
        # The published transcription price is per minute, not a token rate.
        seconds = number(raw.get('seconds', raw.get('duration_seconds')))
        if seconds is None:
            seconds = number(entry.get('duration_seconds'))
        if seconds is not None:
            entry.update(cost_usd=float(Decimal(str(seconds)) * Decimal('.0045') / 60),
                         duration_seconds=seconds, rates={'per_minute': .0045},
                         price_note='Estimated from audio minutes and published per-minute rates; not converted into tokens.')
        return entry
    if inp is None or out is None:
        entry['price_note'] = 'Usage is unreported or missing from historical records; cost is unknown.'
        return entry
    if model == 'text-embedding-3-large':
        rates = (.13, .13, .13, 0)
    elif model == 'jev-1.13.0':
        rates = (.042, .042, .042, 0)
        entry['price_source'] = JEV_PRICE
    else:
        base = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', model)
        if base not in RATES:
            entry['price_note'] = 'No verified rate for this model. Actual tokens are retained; cost is unknown.'
            return entry
        rates = RATES[base]
        tier = entry.get('service_tier') or 'default'
        multiplier = {'auto': 1, 'default': 1, 'standard': 1, 'flex': .5, 'priority': 2, 'fast': 2}.get(tier)
        if multiplier is None:
            entry['price_note'] = 'No verified rate for this service tier.'
            return entry
        rates = tuple(r * multiplier for r in rates)
        if inp > 272000 and entry['operation'] != 'agent':
            rates = (rates[0] * 2, rates[1] * 2, rates[2] * 2, rates[3] * 1.5)
            entry['price_note'] = 'Input exceeds 272K tokens; long-context rates apply.'
        if entry['operation'] == 'agent':
            entry['price_note'] = 'Agent base estimate accounts for cache reads. Unreported cache-write premiums are excluded.'
            if inp > 272000:
                entry['price_note'] += ' Per-request context lengths are unknown; short-context rates are used.'
        elif writes is None:
            entry['price_note'] += ' Cache writes are unreported; associated premiums are excluded.'
    cached, writes = cached or 0, writes or 0
    if cached + writes > inp:
        entry['price_note'] = 'Usage details do not match input totals; cost needs review.'
        return entry
    tokens = (inp - cached - writes, cached, writes, out)
    amount = sum(Decimal(str(n)) * Decimal(str(r)) for n, r in zip(tokens, rates)) / 1_000_000
    entry.update(cost_usd=float(amount), rates=dict(zip(('input', 'cached', 'cache_write', 'output'), rates)))
    return entry


def put(entry, replace_id=None, only_new=False):
    entry = copy.deepcopy(entry)
    with database() as db:
        old = db.execute('SELECT data FROM entries WHERE id=?', (entry['id'],)).fetchone()
        if old:
            previous = json.loads(old[0])
            if only_new or (not entry.get('usage') and previous.get('usage')):
                if replace_id and replace_id != entry['id']:
                    db.execute('DELETE FROM entries WHERE id=?', (replace_id,))
                return previous
            entry['at'] = previous['at']
        entry.setdefault('at', now())
        entry['updated_at'] = now()
        calculate(entry)
        db.execute('INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?)',
                   (entry['id'], entry.get('job_id'), entry.get('file_id'), entry.get('chat_id'),
                    entry.get('turn_id'), json.dumps(entry, ensure_ascii=False)))
        if replace_id and replace_id != entry['id']:
            db.execute('DELETE FROM entries WHERE id=?', (replace_id,))
    return entry


@contextmanager
def request(operation, model, **scope):
    """Persist before dispatch and capture usage before validating generated content."""
    entry = {'id': uuid.uuid4().hex, 'operation': operation, 'model': model, 'status': 'pending', **scope}
    entry = put(entry)
    captured = False

    def capture(value, *, response_id=None, model=None, service_tier=None, duration_seconds=None):
        nonlocal entry, captured
        prior_id = entry['id']
        entry.update(usage=raw_usage(value), status='reported' if raw_usage(value) else 'unknown')
        if isinstance(response_id, str) and response_id:
            entry.update(id='response:' + response_id, response_id=response_id)
        if isinstance(model, str) and model:
            entry['model'] = model
        if isinstance(service_tier, str) and service_tier:
            entry['service_tier'] = service_tier
        if duration_seconds is not None:
            entry['duration_seconds'] = duration_seconds
        entry = put(entry, replace_id=prior_id)
        captured = True
        return entry

    try:
        yield capture
    finally:
        if not captured:
            entry.update(status='unknown', note='No usage received; the request may have failed or disconnected. This is not zero cost.')
            put(entry)


def agent_turn(chat, turn, reported=None):
    turn_id = turn.get('id')
    if not turn_id:
        return
    value = raw_usage(reported if reported is not None else turn.get('usage'))
    subagent = turn.get('subagent_id')
    at = datetime.fromtimestamp(turn['created_at'], timezone.utc).isoformat() if number(turn.get('created_at')) else chat.get('created_at', now())
    # Turn usage includes that agent only. Never also add session aggregate usage.
    return put({'id': 'agent:' + chat['session_id'] + ':' + turn_id, 'operation': 'agent',
                'model': chat['model'] if not subagent else 'unknown-subagent',
                'chat_id': chat['id'], 'turn_id': turn_id, 'subagent_id': subagent,
                'label': 'Subagent' if subagent else 'Agent turn', 'usage': value, 'at': at,
                'status': 'reported' if value else ('pending' if turn.get('status') in {'queued', 'in_progress'} else 'unknown')},
               replace_id=chat.get('pending_usage_id') if not subagent and turn_id == chat.get('turn_id')
               and turn_id != chat.get('usage_previous_turn_id') else None)


def finish_agent_submission(chat):
    # If a connection failed before even receiving a turn ID, retain an unknown charge.
    with database() as db:
        row = db.execute('SELECT data FROM entries WHERE id=?', (chat.get('pending_usage_id'),)).fetchone()
        if row:
            entry = json.loads(row[0])
            entry.update(status='unknown', note='No matching turn received after submission; usage is unconfirmed.')
            db.execute('UPDATE entries SET data=? WHERE id=?', (json.dumps(entry, ensure_ascii=False), entry['id']))


def summarize(entries):
    result = {'entries': len(entries), 'pending': sum(e['status'] == 'pending' for e in entries),
              'unknown_tokens': sum(e['total_tokens'] is None for e in entries),
              'unknown_costs': sum(e['cost_usd'] is None for e in entries)}
    for field in ('input_tokens', 'output_tokens', 'cached_tokens', 'cache_write_tokens', 'reasoning_tokens', 'total_tokens', 'cost_usd'):
        known = [e[field] for e in entries if e.get(field) is not None]
        result[field] = sum(known) if known or not entries else None
    return result


def report(*, job_id=None, file_id=None, chat_id=None, turn_id=None):
    with database() as db:
        all_entries = [json.loads(row[0]) for row in db.execute('SELECT data FROM entries')]
    scope = {k: v for k, v in dict(job_id=job_id, file_id=file_id, chat_id=chat_id, turn_id=turn_id).items() if v}
    entries = [e for e in all_entries if all(e.get(k) == v for k, v in scope.items())]
    groups = {}
    for entry in entries:
        groups.setdefault((entry['operation'], entry['model']), []).append(entry)
    return {'summary': summarize(entries), 'cumulative': summarize(all_entries),
            'groups': [{'operation': op, 'label': LABELS.get(op, op), 'model': model, **summarize(group)}
                       for (op, model), group in groups.items()],
            'entries': sorted(entries, key=lambda e: e['at'], reverse=True)[:100],
            'price_date': PRICE_DATE, 'sources': [OPENAI_PRICE, JEV_PRICE],
            'note': 'USD estimates use published provider rates, not invoices. Only recorded model usage is included; sandbox, GCS, network, taxes, and discounts are excluded. Missing usage is not zero cost.'}


def startup(jobs, documents, chats):
    """One-time import of existing local evidence, without replaying paid calls."""
    with database() as db:
        for row in db.execute('SELECT id, data FROM entries').fetchall():
            entry = json.loads(row[1])
            if entry['status'] == 'pending':
                entry.update(status='unknown', note='The previous run was interrupted; usage is unconfirmed.')
                db.execute('UPDATE entries SET data=? WHERE id=?', (json.dumps(entry, ensure_ascii=False), row[0]))
        if db.execute("SELECT 1 FROM metadata WHERE key='legacy-import-v1'").fetchone():
            return
    for job in jobs.values():
        for item in job['files']:
            scope = {'job_id': job['id'], 'file_id': item['id'], 'label': item['name'],
                     'at': job['updated_at'], 'historical': True}
            for filename, op, model in [('response.json', 'document', job['document_model']),
                                        ('report.json', 'report', config.REPORT_MODEL)]:
                path = config.DATA / job['id'] / item['id'] / filename
                if path.exists():
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if data.get('response_id'):
                        put({'id': 'response:' + data['response_id'], 'operation': op, 'model': data.get('model', model),
                             'usage': data.get('usage') or {}, 'status': 'reported' if data.get('usage') else 'unknown', **scope}, only_new=True)
            if item['kind'] in {'audio', 'video'} and any(p['status'] in {'completed', 'review'} for p in item.get('pages', [])):
                put({'id': 'legacy:audio:' + job['id'] + ':' + item['id'], 'operation': 'transcription',
                     'model': job['transcription_model'], 'usage': {}, 'status': 'unknown',
                     'duration_seconds': item.get('duration'), 'note': 'Historical audio: tokens were not saved; estimate uses recorded duration.', **scope}, only_new=True)
    for doc in documents.values():
        if doc.get('index_status') == 'ready':
            put({'id': 'legacy:index:' + doc['id'], 'operation': 'index_embedding', 'model': config.EMBEDDING_MODEL,
                 'job_id': doc['job_id'], 'file_id': doc['item_id'], 'label': doc['name'],
                 'usage': {}, 'status': 'unknown', 'historical': True, 'at': doc.get('indexed_at', now()),
                 'note': 'Historical indexing did not save embedding usage; cost cannot be reconstructed.'}, only_new=True)
    for chat in chats.values():
        for trace in chat['trace']:
            data = trace['data']
            scope = {'chat_id': chat['id'], 'turn_id': trace.get('turn_id'), 'at': trace['at'], 'historical': True}
            if trace['kind'] == 'file_scored':
                provider = 'openai_decisions' if data.get('judge_provider') == 'openai_decisions' else 'jev'
                put({'id': 'legacy:jev:' + chat['id'] + ':' + trace['id'],
                     'provider': provider, 'operation': provider,
                     'model': data.get('model', config.JEV_MODEL), 'label': data.get('name', 'Historical relevance scoring'),
                     'usage': data.get('usage') or {}, 'status': 'reported' if data.get('usage') else 'unknown', **scope}, only_new=True)
            elif trace['kind'] == 'search_started':
                put({'id': 'legacy:query:' + chat['id'] + ':' + trace['id'], 'operation': 'query_embedding',
                     'model': config.EMBEDDING_MODEL, 'label': 'Historical search', 'usage': {}, 'status': 'unknown', **scope}, only_new=True)
        if chat.get('session_id') and chat.get('turn_id'):
            agent_turn(chat, {'id': chat['turn_id'], 'status': chat['status'], 'usage': chat.get('usage')})
    with database() as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('legacy-import-v1', ?)", (now(),))
