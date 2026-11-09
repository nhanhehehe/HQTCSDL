from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

PATH = '058206001113_Nguyễn Khắc Trường_BT1.docx'

def set_borders(cell, color='808080'):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.first_child_found_in('w:tcBorders')
    if borders is None:
        borders = OxmlElement('w:tcBorders'); tcPr.append(borders)
    for edge in ('top','left','bottom','right','insideH','insideV'):
        el = borders.find(qn('w:'+edge))
        if el is None:
            el = OxmlElement('w:'+edge); borders.append(el)
        el.set(qn('w:val'), 'single'); el.set(qn('w:sz'), '6'); el.set(qn('w:space'), '0'); el.set(qn('w:color'), color)

def format_cell(cell, header=False, center=False):
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    set_borders(cell)
    for p in cell.paragraphs:
        p.paragraph_format.space_after = Pt(0); p.paragraph_format.line_spacing = 1.0
        if center: p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in p.runs:
            r.font.name = 'Aptos'; r.font.size = Pt(10); r.bold = header

doc = Document(PATH)
old_table = doc.tables[0]
# Replace only the table in section 4 with a clean five-column table.
tmp = Document()
table = tmp.add_table(rows=1, cols=5)
table.alignment = WD_TABLE_ALIGNMENT.CENTER
table.autofit = True
headers = ['Khung thời gian', 'T1', 'T2', 'A', 'B']
for i, text in enumerate(headers):
    cell = table.rows[0].cells[i]
    cell.text = text
    format_cell(cell, header=True, center=True)

rows = [
    ('0', '—', '—', '25', '25'),
    ('1', 'Read(A, t)', '—', '25', '25'),
    ('2', 't := t + 100', '—', '25', '25'),
    ('3', 'Write(A, t)', '—', '125', '25'),
    ('4', '—', 'Read(A, s)', '125', '25'),
    ('5', '—', 's := s × 1', '125', '25'),
    ('6', '—', 'Write(A, s)', '125', '25'),
    ('7', '—', 'Read(B, s)', '125', '25'),
    ('8', '—', 's := s × 1', '125', '25'),
    ('9', '—', 'Write(B, s)', '125', '25'),
    ('10', 'Read(B, t)', '—', '125', '25'),
    ('11', 't := t + 100', '—', '125', '25'),
    ('12', 'Write(B, t)', '—', '125', '125'),
]
for row_data in rows:
    row = table.add_row()
    for i, text in enumerate(row_data):
        row.cells[i].text = text
        format_cell(row.cells[i], center=(i == 0 or i >= 3))

old_table._element.getparent().replace(old_table._element, table._element)

for p in doc.paragraphs:
    if p.text.strip().startswith('Kết luận: S2 không khả tuần tự với S1'):
        p.text = 'Kết luận: S2 không khả tuần tự xung đột.'
        p.paragraph_format.space_after = Pt(6)
        break

doc.save(PATH)
print('updated')
