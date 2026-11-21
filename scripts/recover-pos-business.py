"""Preview a selected legacy POS business; merge only with --apply after review.

Preserves current rows and all original IDs. No truncation or database reset.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('--business', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = args.project / 'backend/prisma/dev.db'
    config = dotenv_values(args.project / 'backend/.env')
    url = config['DATABASE_URL']
    parsed = urlparse(url)
    if parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise SystemExit('Recovery is restricted to local PostgreSQL')
    # psql does not accept Prisma's schema query parameter.
    url = url.split('?')[0]
    spec = importlib.util.spec_from_file_location('legacy', args.project / 'backend/scripts/import-sqlite-to-postgres.py')
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    pg_bin = Path(r'C:\Program Files\PostgreSQL\18\bin')
    columns = legacy.postgres_columns(str(pg_bin / 'psql.exe'), url)
    connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    tables = [t for t in legacy.TABLE_ORDER if t in legacy.sqlite_tables(source) and t in columns]
    rows = {t: [dict(r) for r in connection.execute(f'SELECT * FROM "{t}"')] for t in tables}
    fks = {t: [dict(r) for r in connection.execute(f'PRAGMA foreign_key_list("{t}")')] for t in tables}
    selected = {t: set() for t in tables}
    for table in tables:
        for index, row in enumerate(rows[table]):
            if (table == 'Business' and row['id'] == args.business) or row.get('businessId') == args.business:
                selected[table].add(index)
    if not selected.get('Business'):
        raise SystemExit('Selected business does not exist in the legacy database')
    # Include parents and dependent rows without a businessId (bill/menu items,
    # permissions and user/outlet assignments), never another tenant's rows.
    changed = True
    while changed:
        changed = False
        for table in tables:
            for fk in fks[table]:
                parent = fk['table']
                if parent not in rows:
                    continue
                parent_key = fk['to'] or 'id'
                values = {rows[table][i].get(fk['from']) for i in selected[table]} - {None}
                for i, row in enumerate(rows[parent]):
                    if row.get(parent_key) in values and i not in selected[parent]:
                        if (parent == 'Business' and row['id'] != args.business) or ('businessId' in row and row['businessId'] != args.business):
                            raise SystemExit('Cross-business dependency requires manual review')
                        selected[parent].add(i)
                        changed = True
                parents = {rows[parent][i].get(parent_key) for i in selected[parent]}
                for i, row in enumerate(rows[table]):
                    if 'businessId' not in row and table != 'Business' and row.get(fk['from']) in parents and i not in selected[table]:
                        selected[table].add(i)
                        changed = True
    report = {t: len(ids) for t, ids in selected.items() if ids}
    print('Selected legacy business:', args.business)
    print(json.dumps(report, indent=2))
    if not args.apply:
        print('Preview only. No records changed.')
        return
    # Shared roles/permissions and an existing account may have different IDs
    # after the PostgreSQL migration. Reuse their semantic identity and rewrite
    # every selected foreign key, preserving current passwords and account rows.
    aliases = {}
    natural_keys = {'Role': ['name'], 'Permission': ['key'],
                    'User': ['businessId', 'email'], 'Outlet': ['businessId', 'code'],
                    'Product': ['businessId', 'name'], 'InventoryItem': ['businessId', 'name']}
    for table, keys in natural_keys.items():
        query = 'SELECT row_to_json(t) FROM (SELECT ' + ','.join(map(legacy.sql_ident, ['id'] + keys)) + ' FROM ' + legacy.sql_ident(table) + ') t'
        result = subprocess.run([str(pg_bin / 'psql.exe'), url, '-At', '-c', query], check=True, capture_output=True, text=True)
        existing = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        identities = {tuple(str(r[k]).lower() if k == 'email' else r[k] for k in keys): r['id'] for r in existing}
        for i in selected.get(table, []):
            row = rows[table][i]
            identity = tuple(str(row[k]).lower() if k == 'email' else row[k] for k in keys)
            if all(value is not None for value in identity) and identity in identities:
                aliases[(table, row['id'])] = identities[identity]
    identifiers = {old: new for (_, old), new in aliases.items()}
    def rewrite_json(value):
        if isinstance(value, dict):
            return {k: rewrite_json(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite_json(v) for v in value]
        return identifiers.get(value, value) if isinstance(value, str) else value
    for table in tables:
        for i in selected[table]:
            row = rows[table][i]
            if 'id' in row:
                row['id'] = aliases.get((table, row['id']), row['id'])
            for fk in fks[table]:
                value = row.get(fk['from'])
                row[fk['from']] = aliases.get((fk['table'], value), value)
            for key in legacy.JSON_COLUMNS:
                if row.get(key):
                    try:
                        row[key] = json.dumps(rewrite_json(json.loads(row[key])))
                    except (TypeError, ValueError):
                        pass
    print('Existing identities reused:', sum(old != new for (_, old), new in aliases.items()))
    backups = Path(__file__).resolve().parents[1] / 'recovery-backups'
    backups.mkdir(exist_ok=True)
    backup = backups / ('pos-before-recovery-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.dump')
    subprocess.run([str(pg_bin / 'pg_dump.exe'), url, '-Fc', '-f', str(backup)], check=True, capture_output=True)
    statements = ['BEGIN;', 'SET standard_conforming_strings = off;', 'SET CONSTRAINTS ALL DEFERRED;']
    for table in tables:
        for i in sorted(selected[table]):
            row = rows[table][i]
            keys = [k for k in columns[table] if k in row]
            statements.append('INSERT INTO ' + legacy.sql_ident(table) + ' (' + ','.join(map(legacy.sql_ident, keys)) + ') VALUES (' + ','.join(legacy.sql_literal(k, row[k]) for k in keys) + ') ON CONFLICT DO NOTHING;')
    # A uniqueness conflict must not silently drop a referenced legacy identity.
    # If another row owns its email/code, abort the entire merge for review.
    for table in tables:
        for i in sorted(selected[table]):
            row = rows[table][i]
            if 'id' not in row or 'id' not in columns[table]:
                continue
            predicate = '"id" = ' + legacy.sql_literal('id', row['id'])
            if 'businessId' in row:
                predicate += ' AND "businessId" = ' + legacy.sql_literal('businessId', row['businessId'])
            statements.append('DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM ' + legacy.sql_ident(table) + ' WHERE ' + predicate + ") THEN RAISE EXCEPTION 'Legacy identity conflict in " + table + "; recovery cancelled'; END IF; END $$;")
    statements.append('COMMIT;')
    with tempfile.TemporaryDirectory(prefix='pos-recovery-') as temp:
        sql_file = Path(temp) / 'merge.sql'
        sql_file.write_text('\n'.join(statements), encoding='utf-8')
        result = subprocess.run([str(pg_bin / 'psql.exe'), url, '-v', 'ON_ERROR_STOP=1', '-f', str(sql_file)], capture_output=True, text=True)
        if result.returncode:
            # stderr may contain personal data; keep it in the ignored recovery area.
            (backups / 'last-recovery-error.txt').write_text(result.stderr, encoding='utf-8')
            raise SystemExit('Recovery rolled back. See ignored recovery-backups/last-recovery-error.txt')
    print('Merge committed. Existing rows preserved. Backup:', backup.name)


if __name__ == '__main__':
    main()
