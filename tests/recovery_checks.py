"""Backup/restore acceptance confined to disposable coupled_* PostgreSQL databases."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse, unquote
from uuid import uuid4

from bson import BSON, ObjectId, Decimal128, Binary, decode_file_iter
from datetime import datetime, timezone
from pymongo import MongoClient


def verify_recovery(database_url, output, postgres_bin, mongo_url):
    parsed = urlparse(database_url)
    assert parsed.hostname in {'localhost', '127.0.0.1', 'postgres'}
    assert parsed.path.startswith('/coupled_'), 'Recovery acceptance refuses application databases'
    hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process_env = {**os.environ, 'PGPASSWORD': unquote(parsed.password or '')}
    common = ['--host', parsed.hostname, '--port', str(parsed.port or 5432), '--username', unquote(parsed.username or ''), '--no-password']
    original, restored = parsed.path[1:], 'coupled_restore_' + uuid4().hex
    output = Path(output) / 'restore-proof'
    output.mkdir()

    def command(name, args, destination=None):
        binary = str(Path(postgres_bin) / (name + '.exe')) if os.name == 'nt' else shutil.which(name)
        assert binary, name + ' is required for restore acceptance'
        with (output / 'tools.log').open('ab') as errors:
            result = subprocess.run([binary, *common, *args], env=process_env, stdout=destination or subprocess.PIPE,
                                    stderr=errors, check=True, **hidden)
        return result.stdout.decode('utf8').strip() if destination is None else None

    def contents(database):
        tables = command('psql', ['--dbname', database, '--no-psqlrc', '--tuples-only', '--no-align', '--command',
                                 "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).splitlines()
        result = {}
        for table in tables:
            identifier = '"' + table.replace('"', '""') + '"'
            path = output / ('rows-' + hashlib.sha256((database + ':' + table).encode()).hexdigest())
            with path.open('wb') as rows:
                command('psql', ['--dbname', database, '--no-psqlrc', '--tuples-only', '--no-align', '--command',
                                 f'SELECT row_to_json(t)::text FROM public.{identifier} t ORDER BY row_to_json(t)::text'], rows)
            digest = hashlib.sha256()
            with path.open('rb') as rows:
                for block in iter(lambda: rows.read(1024 * 1024), b''):
                    digest.update(block)
            result[table] = digest.hexdigest()
        return result

    dump = output / 'postgres.dump'
    created = False
    try:
        before = contents(original)
        command('pg_dump', ['--dbname', original, '--format=custom', '--no-owner', '--no-acl', '--file', str(dump)])
        command('createdb', [restored])
        created = True
        command('pg_restore', ['--dbname', restored, '--no-owner', '--no-acl', '--exit-on-error', str(dump)])
        after = contents(restored)
        assert before == after, 'Restored PostgreSQL rows differ from their original values'
    finally:
        if created:
            assert restored.startswith('coupled_restore_') and restored != original
            command('dropdb', [restored])

    mongo = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    suffix = uuid4().hex
    source_name, target_name = 'admincore_backup_test_' + suffix, 'admincore_restore_test_' + suffix
    try:
        source, target = mongo[source_name], mongo[target_name]
        fixture = {'_id': ObjectId(), 'invoice_id': 'fixture', 'amount': Decimal128('123.45'),
                   'issued_at': datetime(2026, 10, 29, tzinfo=timezone.utc), 'blob': Binary(b'fixture'),
                   'nested': {'pending': True, 'source_ids': ['a', 'b']}}
        source.receipts.insert_one(fixture)
        source.receipts.create_index('invoice_id', unique=True)
        document_file = output / 'mongo-receipts.bson'
        with document_file.open('wb') as handle:
            for row in source.receipts.find({}):
                handle.write(BSON.encode(row))
        with document_file.open('rb') as handle:
            target.receipts.insert_many(decode_file_iter(handle))
        for index in source.receipts.list_indexes():
            if index['name'] != '_id_':
                target.receipts.create_index(list(index['key'].items()), name=index['name'], unique=index.get('unique', False))
        assert list(source.receipts.find({})) == list(target.receipts.find({})), 'BSON types/identity changed during restore'
        assert target.receipts.index_information()['invoice_id_1']['unique']
        proof = {'postgres_tables_verified': len(before), 'postgres_row_hashes': before,
                 'postgres_dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
                 'mongo_identity_bson_types_and_unique_index': 'passed', 'scope': 'Disposable databases only; writes paused during PostgreSQL backup'}
        (output / 'result.json').write_text(json.dumps(proof, indent=2), encoding='utf8')
        return proof
    finally:
        for name in [source_name, target_name]:
            assert name.endswith(suffix) and name.startswith(('admincore_backup_test_', 'admincore_restore_test_'))
            mongo.drop_database(name)
        mongo.close()
