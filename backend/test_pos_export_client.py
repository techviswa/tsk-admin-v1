import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException
from pos_export_client import await_export


def response(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


class ExportJobTests(unittest.TestCase):
    def test_pending_job_is_polled_and_never_imported_as_an_empty_success(self):
        pending = response(202, {'resource': 'bills', 'business_id': 'a', 'tenant_id': 't', 'snapshot': {'id': 'export-abc', 'retry_after': 1}})
        ready = response(200, {'resource': 'bills', 'business_id': 'a', 'tenant_id': 't', 'items': [{'id': 'receipt'}], 'meta': {'snapshot': {'id': 'export-abc'}}})
        send, params = Mock(return_value=ready), {'limit': 250}
        self.assertIs(await_export(pending, send, params, sleep=Mock()), ready)
        self.assertEqual(params['export_id'], 'export-abc')
        self.assertEqual(params['consistent'], 'true')

    def test_scope_switch_or_missing_job_id_fails_closed(self):
        pending = response(202, {'resource': 'bills', 'business_id': 'a', 'tenant_id': 't', 'snapshot': {'id': 'export-abc'}})
        wrong = response(200, {'resource': 'bills', 'business_id': 'b', 'tenant_id': 't', 'meta': {'snapshot': {'id': 'export-abc'}}})
        with self.assertRaises(HTTPException):
            await_export(pending, Mock(return_value=wrong), {}, sleep=Mock())
        with self.assertRaises(HTTPException):
            await_export(response(202, {'snapshot': {}}), Mock(), {}, sleep=Mock())

    def test_timeout_preserves_a_retryable_pending_status(self):
        pending = response(202, {'resource': 'bills', 'business_id': 'a', 'tenant_id': 't', 'snapshot': {'id': 'export-abc'}})
        with self.assertRaises(HTTPException) as caught:
            await_export(pending, Mock(return_value=pending), {}, seconds=0, sleep=Mock(), now=Mock(return_value=1))
        self.assertEqual(caught.exception.detail['code'], 'POS_EXPORT_PENDING')
