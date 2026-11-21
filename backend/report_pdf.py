"""Shared report schemas and projections for readable PDF and CSV exports."""
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    width: float
    numeric: bool = False


SCHEMAS = {
    'products': [Column('name', 'Product', 3.4), Column('category', 'Category', 2.1),
                 Column('price', 'Unit price', 1.3, True), Column('stock', 'Stock', 1, True), Column('status', 'Status', 1.2)],
    'inventory': [Column('name', 'Inventory item', 3.5), Column('unit', 'Unit', 1.1),
                  Column('stock', 'On hand', 1.2, True), Column('reorder', 'Reorder level', 1.5, True), Column('vendor', 'Supplier', 2.2)],
    'staff': [Column('name', 'Staff member', 2.3), Column('email', 'Email', 3.8),
              Column('role', 'Role', 1.7), Column('status', 'Status', 1.2)],
    'sales': [Column('reference', 'Reference', 2.5), Column('customer', 'Customer', 2.2),
              Column('date', 'Sale date', 2), Column('status', 'Status', 1.3),
              Column('currency', 'Currency', 1), Column('amount', 'Net sales', 1.4, True)],
    'payments': [Column('reference', 'Reference', 2.5), Column('date', 'Payment date', 2),
                 Column('method', 'Method', 1.4), Column('status', 'Status', 1.3),
                 Column('currency', 'Currency', 1), Column('amount', 'Amount', 1.4, True)],
    'outlets': [Column('name', 'Outlet', 2.6), Column('code', 'Code', 1.4),
                Column('address', 'Address', 4), Column('status', 'Status', 1.2)],
    'taxes': [Column('name', 'Tax / charge', 3.5), Column('type', 'Type', 2),
              Column('rate', 'Rate / value', 1.5, True), Column('status', 'Status', 1.3)],
}


def source_value(row, *keys):
    source = row.get('metadata') or {}
    for obj in (source, row):
        for key in keys:
            value = obj.get(key)
            if value is not None and value != '':
                return value
    return ''


def number(value, money=False):
    if value in ('', None):
        return ''
    try:
        # JSON/PostgreSQL floating point arithmetic can leave binary tails such
        # as 5.952000000000001. Keep meaningful precision without printing noise.
        result = Decimal(format(value, '.12g') if isinstance(value, float) else str(value))
        if not result.is_finite():
            return ''
        if money:
            return f'{result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):,.2f}'
        text = f'{result:,f}'
        return text.rstrip('0').rstrip('.') if '.' in text else text
    except (InvalidOperation, ValueError):
        return ''


def status(row):
    active = source_value(row, 'active')
    if isinstance(active, bool):
        return 'Active' if active else 'Inactive'
    return str(source_value(row, 'status') or '').replace('_', ' ').title()


def date_text(value):
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.strftime('%d %b %Y')
    except ValueError:
        return str(value)


def build_report(report_type, rows, *, business_name='All permitted businesses', business_names=None, generated_at=None):
    columns = list(SCHEMAS[report_type])
    if business_names is not None:
        columns.insert(0, Column('business', 'Business', 2.5))
    projected = []
    for row in rows:
        projected.append({
            'name': str(source_value(row, 'name', 'title') or row.get('id') or ''),
            'business': business_name if business_names is None else business_names.get(row.get('business_id'), 'Unassigned'),
            'category': str(source_value(row, 'category')),
            'price': number(source_value(row, 'price'), money=True),
            'stock': number(source_value(row, 'stock')),
            'unit': str(source_value(row, 'unit')),
            'reorder': number(source_value(row, 'reorderLevel', 'reorder_level')),
            'vendor': str(source_value(row, 'vendor', 'supplier_name')),
            'email': str(source_value(row, 'email')),
            'role': str(source_value(row, 'role')).replace('_', ' ').title(),
            'status': status(row),
            'reference': str(source_value(row, 'invoiceNumber', 'invoice_number', 'reference', 'reference_id', 'id')),
            'customer': str(source_value(row, 'customerName', 'customer_name', 'owner_name')),
            'date': date_text(source_value(row, 'paid_at', 'created_at', 'createdAt')),
            'currency': str(source_value(row, 'currency', 'currency_code')),
            'amount': number(source_value(row, 'total', 'amount'), money=True),
            'method': str(source_value(row, 'method', 'payment_method')).replace('_', ' ').title(),
            'code': str(source_value(row, 'code')),
            'address': str(source_value(row, 'address')),
            'type': str(source_value(row, 'type', 'tax_type')).replace('_', ' ').title(),
            'rate': number(source_value(row, 'rate', 'percentage', 'value')),
        })
    summary = [('Records', str(len(rows)))]
    if report_type in {'products', 'staff', 'outlets', 'taxes'}:
        summary.append(('Active', str(sum(item['status'] == 'Active' for item in projected))))
    if report_type == 'products':
        stock = [Decimal(item['stock'].replace(',', '')) for item in projected if item['stock']]
        summary.append(('Units in stock', number(sum(stock)) if stock else 'Not provided'))
    if report_type == 'inventory':
        below = sum(Decimal(item['stock'].replace(',', '')) <= Decimal(item['reorder'].replace(',', ''))
                    for item in projected if item['stock'] and item['reorder'])
        summary.append(('At / below reorder', str(below)))
    if report_type in {'sales', 'payments'} and projected:
        # Never combine money from different or unspecified currencies.
        for currency in sorted({item['currency'] for item in projected if item['currency']}):
            values = [Decimal(item['amount'].replace(',', '')) for item in projected
                      if item['currency'] == currency and item['amount']]
            if values:
                summary.append((f'Total {currency}', number(sum(values), money=True)))
    return {'title': f'{report_type.title()} Report', 'type': report_type, 'business_name': business_name,
            'columns': columns, 'rows': projected, 'summary': summary,
            'generated_at': generated_at or datetime.now(timezone.utc)}


def render_report_csv(report, *, start_index=1, include_header=True):
    buffer = StringIO(newline='')
    writer = csv.writer(buffer)
    columns = list(report['columns'])
    if not any(column.key == 'business' for column in columns):
        columns.insert(0, Column('business', 'Business', 2.5))
    if include_header:
        writer.writerow(['No.'] + [column.label for column in columns])
    for index, row in enumerate(report['rows'], start_index):
        values = [row[column.key] for column in columns]
        values = ["'" + value if not column.numeric and value.startswith(('=', '+', '-', '@')) else value
                  for column, value in zip(columns, values)]
        writer.writerow([index] + values)
    return buffer.getvalue()


def render_report_pdf(report):
    output = BytesIO()
    page_size = landscape(A4) if len(report['columns']) > 5 else A4
    margin = 17*mm
    width = page_size[0] - 2*margin
    navy, blue, muted = map(colors.HexColor, ['#172b4d', '#2563eb', '#64748b'])
    body = ParagraphStyle('Cell', fontName='Helvetica', fontSize=8.5, leading=12, textColor=navy, splitLongWords=True)
    numeric = ParagraphStyle('Number', parent=body, alignment=TA_RIGHT)
    heading = ParagraphStyle('Column', parent=body, fontName='Helvetica-Bold', fontSize=8, textColor=colors.white)
    title = ParagraphStyle('Title', parent=body, fontName='Helvetica-Bold', fontSize=23, leading=29)
    small = ParagraphStyle('Small', parent=body, fontSize=9, leading=14, textColor=muted)
    def paragraph(value, style=body):
        return Paragraph(escape(str(value or '—')).replace('\n', '<br/>'), style)
    generated = report['generated_at'].astimezone(timezone(timedelta(hours=5, minutes=30)))
    stamp = generated.strftime('%d %b %Y, %I:%M %p IST')
    document = SimpleDocTemplate(output, pagesize=page_size, title=report['title'], author='AdminCore',
        leftMargin=margin, rightMargin=margin, topMargin=20*mm, bottomMargin=19*mm)
    story = [paragraph('ADMINCORE  /  BUSINESS REPORTS', ParagraphStyle('Brand', parent=small, textColor=blue, fontSize=8)),
             Spacer(1, 3*mm), paragraph(report['title'], title), Spacer(1, 2*mm),
             paragraph(report['business_name'], ParagraphStyle('Business', parent=body, fontSize=11, leading=16)),
             paragraph(f'Generated {stamp}  ·  {report.get("scope_label", "All available records")}', small), Spacer(1, 6*mm)]
    cards = [[paragraph(label.upper(), ParagraphStyle('MetricLabel', parent=small, fontSize=7)),
              paragraph(value, ParagraphStyle('MetricValue', parent=title, fontSize=16, leading=22))]
             for label, value in report['summary']]
    card_cells = [Table([[label], [value]], style=[('LEFTPADDING', (0,0), (-1,-1), 0),
                  ('RIGHTPADDING', (0,0), (-1,-1), 0)]) for label, value in cards]
    card_count = min(3, len(card_cells))
    card_rows = [card_cells[index:index+card_count] for index in range(0, len(card_cells), card_count)]
    card_rows[-1] += [''] * (card_count - len(card_rows[-1]))
    metrics = Table(card_rows, colWidths=[width/card_count]*card_count)
    metrics.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f1f5f9')),
        ('BOX', (0,0), (-1,-1), .4, colors.HexColor('#e2e8f0')),
        ('LEFTPADDING', (0,0), (-1,-1), 12), ('TOPPADDING', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,-1), 9)]))
    story += [metrics, Spacer(1, 6*mm)]
    weights = [column.width for column in report['columns']]
    widths = [34] + [(width - 34) * weight/sum(weights) for weight in weights]
    headers = [paragraph('#', heading)] + [paragraph(column.label, heading) for column in report['columns']]
    data = [headers]
    for index, row in enumerate(report['rows'], 1):
        data.append([paragraph(str(index), body)] + [paragraph(row[column.key], numeric if column.numeric else body)
                                                    for column in report['columns']])
    if not report['rows']:
        data.append([paragraph('No records found for this selection.', small)] + ['']*len(report['columns']))
    table = LongTable(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitInRow=1)
    rules = [('BACKGROUND', (0,0), (-1,0), navy), ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (0,-1), 6), ('RIGHTPADDING', (0,0), (0,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('LINEBELOW', (0,0), (-1,0), .5, navy),
        ('LINEBELOW', (0,1), (-1,-1), .3, colors.HexColor('#e2e8f0'))]
    if not report['rows']:
        rules.append(('SPAN', (0,1), (-1,1)))
    table.setStyle(TableStyle(rules))
    story.append(table)

    def page_details(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#e2e8f0'))
        canvas.line(margin, 14*mm, page_size[0]-margin, 14*mm)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 10*mm, 'AdminCore · ' + report['title'])
        canvas.drawRightString(page_size[0]-margin, 10*mm, f'Page {doc.page}')
        if doc.page > 1:
            canvas.setFont('Helvetica-Bold', 8)
            canvas.drawString(margin, page_size[1]-12*mm, report['title'] + ' · continued')
            canvas.setFont('Helvetica', 8)
            canvas.drawRightString(page_size[0]-margin, page_size[1]-12*mm, report['business_name'][:80])
        canvas.restoreState()

    document.build(story, onFirstPage=page_details, onLaterPages=page_details)
    return output.getvalue()
