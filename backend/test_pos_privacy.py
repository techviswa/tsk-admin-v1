import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from motor.motor_asyncio import AsyncIOMotorClient
from pos_privacy import ProjectionPrivacy, contact_digest, sanitize_projection


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.marker = {'customer_id': 'customer', 'phone_digest': contact_digest('phone', '+91 98765 43210', 'key'),
                       'email_digest': contact_digest('email', 'PERSON@EXAMPLE.TEST', 'key'), 'erased_at': 'now'}

    def test_nested_receipt_erasure_preserves_financial_evidence(self):
        row = {'id': 'invoice', 'amount': 123.45, 'currency': 'INR', 'owner_name': 'Person',
               'metadata': {'customer_id': 'customer', 'customerName': 'Person', 'customer_phone': '919876543210',
                            'payments': [{'amount': 123.45, 'customerEmail': 'person@example.test'}], 'notes': 'private'}}
        clean = sanitize_projection(row, [self.marker], 'key')
        self.assertEqual(clean['amount'], 123.45)
        self.assertEqual(clean['metadata']['payments'][0]['amount'], 123.45)
        self.assertIsNone(clean['metadata']['customer_phone'])
        self.assertIsNone(clean['metadata']['payments'][0]['customerEmail'])
        self.assertEqual(clean['owner_name'], 'Erased customer')
        self.assertEqual(row['owner_name'], 'Person', 'Input must not be mutated')

    def test_contact_matching_is_normalized_and_does_not_match_names_alone(self):
        self.assertTrue(sanitize_projection({'phone': '+91-98765-43210'}, [self.marker], 'key')['privacy_erased'])
        self.assertTrue(sanitize_projection({'email': ' Person@Example.Test '}, [self.marker], 'key')['privacy_erased'])
        row = {'name': 'Person', 'phone': '1111111111'}
        self.assertEqual(sanitize_projection(row, [self.marker], 'key'), row)

    def test_legacy_customer_identity_cannot_retain_literal_contact(self):
        row = {'id': 'local', 'pos_external_id': 'customer-person@example.test', 'phone': '919876543210',
               'title': 'Person', 'metadata': {'id': 'person@example.test', 'phone': '919876543210', 'name': 'Person'}}
        clean = sanitize_projection(row, [self.marker], 'key', customer=True)
        self.assertFalse(clean['source_present'])
        self.assertEqual(clean['id'], 'local')
        self.assertNotIn('person@example', str(clean))
        self.assertNotIn('919876543210', str(clean))

    def test_feedback_is_erased_by_bill_reference_without_phone(self):
        clean = sanitize_projection({'metadata': {'billId': 'receipt', 'customerName': 'Person', 'rating': 5, 'comment': 'Private text'}},
                                    [{**self.marker, 'bill_ids': ['receipt']}], 'key')
        self.assertEqual(clean['metadata']['customerName'], 'Erased customer')
        self.assertEqual(clean['metadata']['rating'], 5)
        self.assertIsNone(clean['metadata']['comment'])

    def test_delivery_details_are_erased_without_changing_amounts_or_product_names(self):
        clean = sanitize_projection({'amount': 17, 'metadata': {'customer_id': 'customer',
            'delivery_address': 'Private street', 'deliveryAddress': 'Legacy address',
            'delivery_instructions': 'Private instructions', 'items': [{'name': 'Tea', 'quantity': 1}]}}, [self.marker], 'key')
        self.assertEqual(clean['amount'], 17)
        for field in ['delivery_address', 'deliveryAddress', 'delivery_instructions']:
            self.assertIsNone(clean['metadata'][field])
        self.assertEqual(clean['metadata']['items'][0]['name'], 'Tea')

    def test_a_new_customer_can_reuse_a_contact_after_erasure_without_losing_new_records(self):
        marker = {**self.marker, 'erased_at': '2026-10-01T00:00:00Z'}
        new = {'id': 'new-person', 'created_at': '2026-10-02T00:00:00Z', 'metadata': {'customer_id': 'new-person', 'customer_phone': '919876543210'}}
        self.assertEqual(sanitize_projection(new, [marker], 'key', customer=True), new)
        new['metadata']['customer_id'] = 'customer'
        self.assertTrue(sanitize_projection(new, [marker], 'key')['privacy_erased'])


@unittest.skipUnless(os.environ.get('RUN_PROJECTION_DB_TESTS') == '1', 'Isolated MongoDB acceptance enabled explicitly')
class PrivacyDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_marker_survives_worker_reconstruction_and_blocks_stale_projection(self):
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=5000)
        name = 'privacy_acceptance_' + uuid4().hex
        db = client[name]
        try:
            rows = [{'id': 'ours', 'business_id': 'a', 'amount': 17, 'updated_at': 'before', 'metadata': {'customer_id': 'person', 'customerName': 'Secret'}},
                    {'id': 'other', 'business_id': 'b', 'amount': 20, 'updated_at': 'before', 'metadata': {'customer_id': 'person', 'customerName': 'Other business'}}]
            await db.pos_bills.insert_many(rows)
            privacy = ProjectionPrivacy(db, 'key', ['pos_bills'])
            await privacy.apply({'customer_id': 'person', 'erased_at': 'now'}, 'a')
            self.assertEqual((await db.pos_bills.find_one({'id': 'ours'}))['metadata']['customerName'], 'Erased customer')
            self.assertEqual((await db.pos_bills.find_one({'id': 'other'}))['metadata']['customerName'], 'Other business')
            restarted = ProjectionPrivacy(db, 'key', ['pos_bills'])
            stale = await restarted.sanitize({'metadata': {'customer_id': 'person', 'customerName': 'Old payload'}, 'amount': 17}, 'a')
            self.assertEqual(stale['metadata']['customerName'], 'Erased customer')
            self.assertEqual(stale['amount'], 17)
            await restarted.apply({'customer_id': 'person', 'erased_at': 'now'}, 'a')
            self.assertEqual(await db.pos_privacy_erasures.count_documents({}), 1)
            import hashlib
            import hmac
            old_key = 'previous-bridge-key'
            marker = {'customer_id': 'old-person', 'erased_at': '2026-10-01T00:00:00Z',
                      'phone_digest': hmac.new(old_key.encode(), b'phone:919876543210', hashlib.sha256).hexdigest(),
                      'digest_key_id': hashlib.sha256(old_key.encode()).hexdigest()[:16]}
            rotated = ProjectionPrivacy(db, 'new-key', ['pos_bills'], [old_key])
            await rotated.apply(marker, 'rotated')
            contact = {'created_at': '2026-09-01T00:00:00Z', 'metadata': {'customer_phone': '919876543210'}}
            self.assertTrue((await rotated.sanitize(contact, 'rotated'))['privacy_erased'])
            legacy = {key: value for key, value in marker.items() if key != 'digest_key_id'}
            await rotated.apply(legacy, 'legacy-rotated')
            self.assertTrue((await rotated.sanitize(contact, 'legacy-rotated'))['privacy_erased'])
            with self.assertRaises(ValueError):
                await ProjectionPrivacy(db, 'new-key', ['pos_bills']).sanitize(contact, 'rotated')
        finally:
            await client.drop_database(name)
            client.close()
