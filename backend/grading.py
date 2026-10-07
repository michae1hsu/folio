"""Full-input score adapters for JEV and the public OpenAI Decisions API."""
import json
import math
import os

import httpx
from openai import APIStatusError

from . import config, usage
from .models import api_client

PROVIDERS = ('jev', 'openai_decisions')
DECISIONS_MODEL = 'gpt-6-luna'  # The only model supported by Decisions at launch.


def validate_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError('Grading provider must be JEV or OpenAI Decisions.')
    return provider


def model_for(provider):
    return config.JEV_MODEL if validate_provider(provider) == 'jev' else DECISIONS_MODEL


def label_for(provider):
    return 'JEV' if validate_provider(provider) == 'jev' else 'OpenAI Decisions'


def build_request(provider, state, questions, model=None):
    validate_provider(provider)
    if provider == 'jev':
        return {'model': model or model_for(provider), 'state': state, 'questions': questions}
    return {'model': model or model_for(provider), 'input': json.dumps(state, ensure_ascii=False),
            'questions': [{'name': name, 'type': 'score', 'instructions': question['instructions'],
                           'levels': [{'label': str(i), 'description': criterion}
                                      for i, criterion in enumerate(question['criteria'])]}
                          for name, question in questions.items()]}


def parse_scores(data, required, provider='jev'):
    label = label_for(provider)
    answers = data.get('answers') if isinstance(data, dict) else None
    if provider == 'openai_decisions':
        if (not isinstance(answers, list) or len(answers) != len(required)
                or any(not isinstance(a, dict) for a in answers)
                or [a.get('name') for a in answers] != list(required)):
            raise ValueError(label + ' returned mismatched grading questions, count, or order.')
        answers = {a['name']: a for a in answers}
    if not isinstance(answers, dict):
        raise ValueError(label + ' did not return valid scores.')
    result = {}
    for name in required:
        answer = answers.get(name)
        if not isinstance(answer, dict):
            raise ValueError(label + ' did not return valid scores.')
        if answer.get('type') == 'refusal':
            raise ValueError(label + ' refused grading. This remains unscored, not a zero score.')
        probabilities = answer.get('probabilities')
        if provider == 'openai_decisions':
            if (not isinstance(probabilities, list) or len(probabilities) != 4
                    or any(not isinstance(p, dict) or type(p.get('value')) is not int
                           or p.get('value') not in range(4) or p.get('label') != str(p.get('value'))
                           for p in probabilities)
                    or {p['value'] for p in probabilities} != {0, 1, 2, 3}):
                raise ValueError(label + ' returned level probabilities outside the 0–3 rubric.')
            probabilities = {str(p['value']): p.get('probability') for p in probabilities}
        score, confidence = answer.get('score'), answer.get('confidence')
        if not isinstance(probabilities, dict):
            raise ValueError(label + ' did not return a valid probability distribution.')
        values = [score, confidence, *probabilities.values()]
        if (answer.get('type') != 'score' or set(probabilities) != {'0', '1', '2', '3'}
                or not all(type(n) in (int, float) and math.isfinite(n) for n in values)
                or not 0 <= score <= 3 or not 0 <= confidence <= 1
                or any(not 0 <= p <= 1 for p in probabilities.values())
                or abs(sum(probabilities.values()) - 1) > .02):
            raise ValueError(label + ' returned an invalid full-document score or probability distribution.')
        if provider == 'openai_decisions' and abs(score - sum(int(k) * p for k, p in probabilities.items())) > .02:
            raise ValueError(label + ' returned a score inconsistent with the level probabilities.')
        result[name] = {'score': score, 'confidence': confidence, 'probabilities': probabilities,
                        'grade': int(max(probabilities, key=probabilities.get))}
    return result


def evaluate(provider, state, questions, *, operation, model=None, scope=None, transport=None,
             log_request=lambda value: None, log_response=lambda value: None):
    """One paid request, no implicit retries, truncation, or provider fallback."""
    provider = validate_provider(provider)
    model = model or model_for(provider)
    request = build_request(provider, state, questions, model)
    log_request(request)
    label = label_for(provider)
    key = os.getenv('JEV_API_KEY') or os.getenv('TYPESAFE_API_KEY')
    if provider == 'jev' and not key:
        raise ValueError('JEV_API_KEY is not configured. No substitute model or fabricated scores will be used.')
    with usage.request(operation, model, provider=provider, **(scope or {})) as capture:
        if provider == 'jev':
            with httpx.Client(timeout=120, transport=transport) as client:
                response = client.post(config.JEV_URL, headers={'Authorization': f'Bearer {key}'}, json=request)
            status = response.status_code
            data = response.json() if status == 200 else None
        else:
            try:
                # Public SDK POST supports this endpoint even before typed Decisions methods.
                with api_client(transport=transport) as client:
                    data = client.post('/decisions', cast_to=dict, body=request, options={'timeout': 120})
                status = 200
            except APIStatusError as error:
                status, data = error.status_code, None
        if status != 200:
            message = {400: 'rejected the grading request. No truncation was applied',
                       401: 'authentication failed', 402: 'quota exceeded', 403: 'denied project access',
                       404: 'endpoint or model is not available to this project',
                       413: 'complete document or context exceeds the request limit. No truncation was applied',
                       422: 'rejected the complete input, possibly exceeding its context limit. No truncation was applied',
                       429: 'rate or quota limited. Retry manually later'}.get(status, f'grading failed (HTTP {status})')
            raise ValueError(label + ' ' + message + '. This remains unscored.')
        capture(data.get('usage') if isinstance(data, dict) else None,
                model=data.get('model') if isinstance(data, dict) else None)
        log_response(data)
    return parse_scores(data, questions, provider), data
