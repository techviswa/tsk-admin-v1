import os
import csv
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pypdf import PdfReader
from report_pdf import build_report, number, render_report_csv, render_report_pdf

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "report_export_test"}):
    import server


class TruthlessCollection:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.position = 0

    def __bool__(self):
        raise NotImplementedError('Mongo collections cannot be tested as booleans')

    def find(self, query, projection):
        self.query = query
        return self

    def sort(self, *args):
        return self

    async def to_list(self, length):
        result = self.rows[self.position:self.position + length]
        self.position += len(result)
        return result

    async def close(self):
        pass


class ReportPdfTests(unittest.TestCase):
    def test_quantity_precision_removes_float_noise_without_losing_small_units(self):
        self.assertEqual(number(5.952000000000001), '5.952')
        self.assertEqual(number(0.000001), '0.000001')
        self.assertEqual(number(0), '0')
        self.assertEqual(number(None), '')

    def test_valid_paginated_pdf_keeps_rows_after_35_and_long_names(self):
        rows = [{'name': f'Product {i} - ' + 'long product description ' * 8 + f'END-{i}',
                 'category': 'Meals', 'price': 10, 'stock': 0} for i in range(46)]
        content = render_report_pdf(build_report('products', rows, business_name='Cafe <One> & Kitchen'))
        document = PdfReader(BytesIO(content), strict=True)
        self.assertGreater(len(document.pages), 1)
        text = ''.join(page.extract_text() for page in document.pages)
        self.assertIn('END-45', text)
        self.assertIn('Cafe <One> & Kitchen', text)
        for page in document.pages:
            self.assertIn('Unit price', page.extract_text())

    def test_empty_report_is_valid_and_explicit(self):
        document = PdfReader(BytesIO(render_report_pdf(build_report('sales', []))), strict=True)
        self.assertIn('No records found', document.pages[0].extract_text())

    def test_report_fields_use_source_data_and_preserve_zero_values(self):
        row = {'title': 'Imported item', 'amount': 99, 'metadata': {
            'name': 'Milk', 'stock': 0, 'unit': 'litre', 'reorderLevel': 5, 'vendor': 'Dairy'}}
        report = build_report('inventory', [row])
        self.assertEqual(report['rows'][0]['stock'], '0')
        self.assertEqual(report['rows'][0]['unit'], 'litre')
        self.assertIn(('At / below reorder', '1'), report['summary'])
        data = list(csv.DictReader(render_report_csv(report).splitlines()))
        self.assertEqual(data[0]['Inventory item'], 'Milk')
        self.assertEqual(data[0]['On hand'], '0')

    def test_mixed_currencies_have_separate_totals(self):
        report = build_report('sales', [{'amount': '10.25', 'currency': 'INR'},
            {'amount': '2.50', 'currency': 'USD'}, {'amount': '100'}])
        self.assertIn(('Total INR', '10.25'), report['summary'])
        self.assertIn(('Total USD', '2.50'), report['summary'])
        self.assertEqual(len(report['summary']), 3)


class ReportExportTests(unittest.IsolatedAsyncioTestCase):
    async def export(self, report_type, format='pdf', count=46):
        collection = TruthlessCollection([{'id': str(i), 'name': f'Product {i}', 'reference': f'Product {i}', 'price': 10,
                                          'business_id': 'allowed'} for i in range(count)])
        names = ['pos_bills', 'products', 'pos_inventory_admin', 'pos_payments',
                 'pos_staff_shifts', 'outlets', 'pos_taxes_charges']
        database = SimpleNamespace(**{name: collection for name in names})
        database.businesses = SimpleNamespace(find=Mock(return_value=SimpleNamespace(
            to_list=AsyncMock(return_value=[{'id': 'allowed', 'name': 'Allowed Cafe'}]))))
        scope = {'business_id': {'$in': ['allowed']}, 'tenant_scope_status': {'$ne': 'quarantined'}}
        with patch.object(server, 'db', database), \
             patch.object(server, 'get_current_user', AsyncMock(return_value={'role': 'manager'})), \
             patch.object(server, 'require_module_for_business_scope', AsyncMock()), \
             patch.object(server, 'pos_read_filter', AsyncMock(return_value=scope)), \
             patch.object(server, 'summarize_pos_collection', AsyncMock(return_value={'invalid_amounts': 0})):
            response = await server.export_reports(None, business_id=None, report_type=report_type, format=format, outlet_id=None, date_from=None, date_to=None)
            if format == 'csv':
                content = ''.join([chunk async for chunk in response.body_iterator])
                response.body = content.encode()
        self.assertEqual(collection.query, {**scope, **({'status': {'$nin': ['void', 'cancelled', 'canceled']}} if report_type == 'sales' else {})})
        return response

    async def test_all_report_types_download_valid_pdf_and_csv(self):
        for report in ['sales', 'products', 'inventory', 'payments', 'staff', 'outlets', 'taxes']:
            with self.subTest(report=report):
                pdf = await self.export(report)
                self.assertEqual(pdf.media_type, 'application/pdf')
                self.assertIn(f'{report}-report.pdf', pdf.headers['content-disposition'])
                text = ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf.body), strict=True).pages)
                self.assertIn('Product 45', text)
                csv = await self.export(report, 'csv')
                self.assertEqual(len(csv.body.decode().splitlines()), 47)

    async def test_invalid_format_is_rejected(self):
        with self.assertRaises(server.HTTPException) as error:
            await self.export('products', 'html')
        self.assertEqual(error.exception.status_code, 400)

    async def test_oversized_report_is_not_silently_truncated(self):
        with self.assertRaises(server.HTTPException) as error:
            await self.export('products', count=10001)
        self.assertEqual(error.exception.status_code, 413)

    async def test_large_csv_stream_has_one_header_and_continuous_numbers(self):
        response = await self.export('products', 'csv', count=1501)
        rows = list(csv.DictReader(response.body.decode().splitlines()))
        self.assertEqual(len(rows), 1501)
        self.assertEqual(rows[-1]['No.'], '1501')
        self.assertEqual(rows[-1]['Product'], 'Product 1500')
