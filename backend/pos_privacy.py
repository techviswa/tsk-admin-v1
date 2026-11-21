"""Durable customer-erasure barriers for retained POS projections."""
import hashlib
import hmac
import re
from datetime import datetime, timezone

PHONE_KEYS = {'phone', 'customerPhone', 'customer_phone', 'guest_phone', 'guestPhone', 'contact'}
EMAIL_KEYS = {'email', 'customerEmail', 'customer_email', 'guest_email', 'guestEmail'}
IDENTITY_KEYS = {'customerId', 'customer_id'}
PRIVATE_KEYS = PHONE_KEYS | EMAIL_KEYS | {'customerName', 'customer_name', 'guest_name', 'guestName', 'address',
    'customer_address', 'customerAddress', 'delivery_address', 'deliveryAddress', 'recipient_name', 'recipientName'}
TEXT_KEYS = {'notes', 'note', 'special_requests', 'specialRequests', 'comment', 'delivery_instructions',
             'deliveryInstructions', 'customer_notes', 'customerNotes'}


def contact_digest(kind, value, secret):
    normalized = re.sub(r'\D', '', str(value or '')) if kind == 'phone' else str(value or '').strip().lower()
    return hmac.new(secret.encode(), f'{kind}:{normalized}'.encode(), hashlib.sha256).hexdigest() if normalized else None


def matches_contact(kind, value, marker, secret):
    expected = marker.get(kind + '_digest')
    keys = marker.get('_digest_secrets') or (marker.get('_digest_secret', secret),)
    return bool(expected) and any(contact_digest(kind, value, key) == expected for key in keys)


def matches_marker(value, marker, secret, created_at=None):
    if isinstance(value, list):
        return any(matches_marker(item, marker, secret, created_at) for item in value)
    if not isinstance(value, dict):
        return False
    created_at = value.get('created_at') or value.get('createdAt') or value.get('first_seen_at') or created_at
    newer = False
    try:
        stamp = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
        erased = datetime.fromisoformat(str(marker.get('erased_at')).replace('Z', '+00:00'))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        erased = erased if erased.tzinfo else erased.replace(tzinfo=timezone.utc)
        newer = stamp.timestamp() > erased.timestamp()
    except (TypeError, ValueError):
        pass
    for key, item in value.items():
        if isinstance(item, str) and ((key in {'billId', 'bill_id', 'invoiceId', 'invoice_id', 'id', 'pos_external_id'} and item in marker.get('bill_ids', []))
                                     or (key in {'orderId', 'order_id', 'id', 'pos_external_id'} and item in marker.get('order_ids', []))):
            return True
        if key in IDENTITY_KEYS and item == marker.get('customer_id'):
            return True
        for kind, keys in [('phone', PHONE_KEYS), ('email', EMAIL_KEYS)]:
            expected = marker.get(kind + '_digest')
            if not newer and key in keys and expected and matches_contact(kind, item, marker, secret):
                return True
        if isinstance(item, (dict, list)) and matches_marker(item, marker, secret, created_at):
            return True
    return False


def redact_contacts(value, marker, secret):
    if isinstance(value, dict):
        return {key: redact_contacts(item, marker, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_contacts(item, marker, secret) for item in value]
    if not isinstance(value, str):
        return value
    def replace(match):
        token = match.group()
        kind = 'email' if '@' in token else 'phone'
        return '[erased]' if matches_contact(kind, token, marker, secret) else token
    return re.sub(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+|\+?\d[\d ()-]{5,}\d', replace, value)


def erase_receipt(value):
    if isinstance(value, list):
        return [erase_receipt(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: ('Erased customer' if 'name' in key.lower() else None) if key in PRIVATE_KEYS
            else None if key in TEXT_KEYS else erase_receipt(item) for key, item in value.items()}


def sanitize_projection(row, markers, secret, *, customer=False):
    if not any(matches_marker(row, marker, secret) or
               (customer and str(row.get('pos_external_id') or row.get('id')) == marker.get('customer_id')) for marker in markers):
        return row
    clean = erase_receipt(row)
    clean['privacy_erased'] = True
    for key in ['owner_name', 'title', 'name']:
        if key in clean:
            clean[key] = 'Erased customer'
    if customer:
        clean.update(email=None, phone=None, contact=None, notes=None)
        # Legacy receipt-derived CRM IDs sometimes contained the literal phone/email.
        for key in ['pos_external_id', 'external_id']:
            if clean.get(key):
                clean[key] = 'erased-' + hmac.new(secret.encode(), str(clean[key]).encode(), hashlib.sha256).hexdigest()
        clean['source_present'] = False
        if isinstance(clean.get('metadata'), dict):
            clean['metadata']['id'] = 'erased-customer'
            clean['metadata']['name'] = 'Erased customer'
    return clean


class ProjectionPrivacy:
    def __init__(self, db, secret, collections, previous_keys=()):
        self.db, self.secret, self.collections = db, secret, tuple(set(collections))
        self.digest_keys = {hashlib.sha256(key.encode()).hexdigest()[:16]: key for key in (secret, *previous_keys) if key}

    def attach_digest_key(self, marker):
        identity = marker.get('digest_key_id')
        if identity and identity not in self.digest_keys:
            raise ValueError('A retained privacy marker needs its previous bridge key configured; erasure remains unfinished')
        return {**marker, '_digest_secret': self.digest_keys.get(identity, self.secret),
                '_digest_secrets': (self.digest_keys[identity],) if identity else tuple(self.digest_keys.values())}

    async def markers(self, business_id):
        rows = await self.db.pos_privacy_erasures.find({'business_id': business_id}, {'_id': 0}).to_list(None)
        return [self.attach_digest_key(row) for row in rows]

    async def sanitize(self, row, business_id, *, customer=False):
        return sanitize_projection(row, await self.markers(business_id), self.secret, customer=customer)

    async def apply(self, marker, business_id):
        # The scope was verified at the bridge boundary. Persist before scrubbing:
        # interruption retries cleanup, while subsequent imports already see the barrier.
        doc = {key: marker.get(key) for key in ['customer_id', 'phone_digest', 'email_digest', 'digest_key_id', 'erased_at', 'bill_ids', 'order_ids']}
        doc['bill_ids'], doc['order_ids'] = doc['bill_ids'] or [], doc['order_ids'] or []
        if not doc['customer_id'] or not doc['erased_at']:
            raise ValueError('Invalid erasure marker')
        identity = f"{business_id}:{marker.get('id') or doc['customer_id']}"
        await self.db.pos_privacy_erasures.update_one({'_id': identity}, {'$set': {**doc, 'business_id': business_id}}, upsert=True)
        doc = self.attach_digest_key(doc)
        for name in self.collections:
            async for row in self.db[name].find({'business_id': business_id}):
                clean = sanitize_projection(row, [doc], self.secret, customer=name in {'pos_customers', 'pos_customer_profiles'})
                if clean != row:
                    # A simultaneous import must not replace a newer projection with this scan's copy.
                    await self.db[name].update_one({'_id': row['_id'], 'updated_at': row.get('updated_at')},
                                                   {'$set': {key: value for key, value in clean.items() if key != '_id'}})
        for name in ['pos_change_jobs', 'pos_bridge_sync_runs', 'audit_logs']:
            async for row in self.db[name].find({'business_id': business_id}):
                clean = redact_contacts(row, doc, self.secret)
                if matches_marker(row, doc, self.secret):
                    for field in ['details', 'metadata', 'message', 'last_error', 'errors']:
                        if field in clean:
                            clean[field] = {'privacy_erased': True} if isinstance(clean[field], dict) else None
                if clean != row:
                    await self.db[name].update_one({'_id': row['_id'], 'updated_at': row.get('updated_at')},
                                                   {'$set': {key: value for key, value in clean.items() if key != '_id'}})
        return identity
