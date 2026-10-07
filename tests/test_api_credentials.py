import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import models
from backend.app import app


class ApiCredentialTests(unittest.TestCase):
    def test_model_picker_with_blank_settings_does_not_request_remote_models(self):
        api = TestClient(app)
        self.addCleanup(api.close)
        with patch.dict(os.environ, {}, clear=True), patch('backend.app.api_client') as client:
            response = api.get('/api/agent/models')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['verified'])
        self.assertIn('OPENAI_API_KEY', response.json()['error'])
        client.assert_not_called()

    def test_explicit_direct_key_preserves_inherited_proxy_key_and_settings(self):
        values = {'OPENAI_API_KEY': 'proxy-test-key', 'FOLIO_OPENAI_DIRECT_KEY': 'direct-test-key',
                  'HTTPS_PROXY': 'http://proxy.example:8080'}
        with patch.dict(os.environ, values, clear=True), patch.object(models, 'OpenAI') as sdk:
            models.api_client()
            self.assertEqual(sdk.call_args.kwargs['api_key'], 'direct-test-key')
            self.assertEqual(sdk.call_args.kwargs['max_retries'], 0)
            self.assertEqual(os.environ['OPENAI_API_KEY'], values['OPENAI_API_KEY'])
            self.assertEqual(os.environ['HTTPS_PROXY'], values['HTTPS_PROXY'])

    def test_without_direct_key_sdk_retains_its_normal_credential_selection(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'proxy-test-key'}, clear=True), patch.object(models, 'OpenAI') as sdk:
            models.api_client()
            self.assertNotIn('api_key', sdk.call_args.kwargs)
            self.assertTrue(models.openai_key_configured())

    def test_failed_direct_credential_does_not_retry_with_another_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'proxy-test-key', 'FOLIO_OPENAI_DIRECT_KEY': 'direct-test-key'}, clear=True), \
                patch.object(models, 'OpenAI', side_effect=ValueError('test failure')) as sdk:
            with self.assertRaises(ValueError):
                models.api_client()
            sdk.assert_called_once()

    def test_direct_only_configuration_is_accepted_by_health_and_job_start(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {'FOLIO_OPENAI_DIRECT_KEY': 'direct-test-key'}, clear=True), \
                patch('backend.app.get_job', return_value={'id': 'test-job'}), \
                patch('backend.app.jobs.start_job', return_value={'status': 'queued'}) as start:
            self.assertTrue(client.get('/api/health').json()['key_configured'])
            response = client.post('/api/jobs/test-job/start', headers={'X-Folio-Request': '1'}, json={})
            self.assertEqual(response.status_code, 200)
            start.assert_called_once()

    def test_empty_credentials_disable_health_and_job_start(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {'OPENAI_API_KEY': ' ', 'FOLIO_OPENAI_DIRECT_KEY': ' '}, clear=True):
            self.assertFalse(client.get('/api/health').json()['key_configured'])
            response = client.post('/api/jobs/test-job/start', headers={'X-Folio-Request': '1'}, json={})
            self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
