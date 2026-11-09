from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import glob
import os

PATH = glob.glob('058206001113*.docx')[0]
print('path', repr(PATH))
print('exists', os.path.exists(PATH))

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
            r.font.name = 'Times New Roman'; r._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman'); r.font.size = Pt(10); r.bold = header

doc = Document(PATH)
old_table = doc.tables[0]
tmp = Document()
table = tmp.add_table(rows=1, cols=5)
table.alignment = WD_TABLE_ALIGNMENT.CENTER; table.autofit = True
headers = ['T1', 'T2', 'A', 'B', 't']
for i, text in enumerate(headers):
    table.rows[0].cells[i].text = text
    format_cell(table.rows[0].cells[i], header=True, center=True)

rows = [
    ('—', '—', '25', '25', '—'),
    ('Read(A, t)', '—', '25', '25', '25'),
    ('t := t + 100', '—', '25', '25', '125'),
    ('Write(A, t)', '—', '125', '25', '125'),
    ('—', 'Read(A, s)', '125', '25', '125'),
    ('—', 's := s × 1', '125', '25', '125'),
    ('—', 'Write(A, s)', '125', '25', '125'),
    ('—', 'Read(B, s)', '125', '25', '125'),
    ('—', 's := s × 1', '125', '25', '125'),
    ('—', 'Write(B, s)', '125', '25', '125'),
    ('Read(B, t)', '—', '125', '25', '25'),
    ('t := t + 100', '—', '125', '25', '125'),
    ('Write(B, t)', '—', '125', '125', '125'),
]
for data in rows:
    row = table.add_row()
    for i, text in enumerate(data):
        row.cells[i].text = text
        format_cell(row.cells[i], center=(i >= 2))
old_table._element.getparent().replace(old_table._element, table._element)

# Set the requested font throughout the user's edited document.
for p in doc.paragraphs:
    for r in p.runs:
        r.font.name = 'Times New Roman'; r._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Times New Roman')
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.name = 'Times New Roman'; r._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Times New Roman')
for style_name in ['Normal', 'Title', 'Heading 1', 'Heading 2', 'List Bullet', 'List Bullet 2']:
    if style_name in doc.styles:
        style = doc.styles[style_name]
        style.font.name = 'Times New Roman'
        style._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')

doc.save(PATH)
print('updated')
