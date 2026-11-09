from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.style import WD_STYLE_TYPE

OUT = 'Bai_1_lich_tuan_tu.docx'

def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn('w:shd'))
    if shd is None:
        shd = OxmlElement('w:shd'); tcPr.append(shd)
    shd.set(qn('w:fill'), fill)

def set_cell_border(cell, color='D9D9D9', sz='6'):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr()
    borders = tcPr.first_child_found_in('w:tcBorders')
    if borders is None:
        borders = OxmlElement('w:tcBorders'); tcPr.append(borders)
    for edge in ('top','left','bottom','right','insideH','insideV'):
        tag = 'w:' + edge
        el = borders.find(qn(tag))
        if el is None:
            el = OxmlElement(tag); borders.append(el)
        el.set(qn('w:val'), 'single'); el.set(qn('w:sz'), sz); el.set(qn('w:space'), '0'); el.set(qn('w:color'), color)

def set_cell_margins(cell, top=90, start=110, bottom=90, end=110):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in('w:tcMar')
    if tcMar is None:
        tcMar = OxmlElement('w:tcMar'); tcPr.append(tcMar)
    for m, v in [('top', top), ('start', start), ('bottom', bottom), ('end', end)]:
        node = tcMar.find(qn('w:'+m))
        if node is None:
            node = OxmlElement('w:'+m); tcMar.append(node)
        node.set(qn('w:w'), str(v)); node.set(qn('w:type'), 'dxa')

def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr(); tblHeader = OxmlElement('w:tblHeader'); tblHeader.set(qn('w:val'), 'true'); trPr.append(tblHeader)

def set_widths(table, widths):
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)

def style_table(table, header=True):
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for r, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_border(cell); set_cell_margins(cell)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.05
                for run in p.runs:
                    run.font.name = 'Aptos'; run.font.size = Pt(10)
        if header and r == 0:
            shade(row.cells[0], '1F4E78')
            for c in row.cells[1:]: shade(c, '1F4E78')
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in p.runs:
                        run.bold = True; run.font.color.rgb = RGBColor(255,255,255); run.font.size = Pt(10)
            set_repeat_table_header(row)
        elif r % 2 == 0:
            for cell in row.cells: shade(cell, 'F3F6F9')

def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style=f'Heading {level}')
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8); p.paragraph_format.space_after = Pt(5)
    run = p.add_run(text); run.font.name = 'Aptos Display'; run.font.color.rgb = RGBColor(0,0,0)
    return p

def add_body(doc, text, bold_prefix=None):
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(6); p.paragraph_format.line_spacing = 1.12
    if bold_prefix and text.startswith(bold_prefix):
        p.add_run(bold_prefix).bold = True; p.add_run(text[len(bold_prefix):])
    else: p.add_run(text)
    return p

doc = Document()
sec = doc.sections[0]
sec.top_margin = Inches(0.65); sec.bottom_margin = Inches(0.65); sec.left_margin = Inches(0.75); sec.right_margin = Inches(0.75)
styles = doc.styles
styles['Normal'].font.name = 'Aptos'; styles['Normal'].font.size = Pt(11)
for nm, size in [('Title', 18), ('Heading 1', 14), ('Heading 2', 12)]:
    styles[nm].font.name = 'Aptos Display'; styles[nm].font.size = Pt(size); styles[nm].font.bold = True; styles[nm].font.color.rgb = RGBColor(0,0,0)
    ppr = styles[nm]._element.get_or_add_pPr()
    pbdr = ppr.find(qn('w:pBdr'))
    if pbdr is not None: ppr.remove(pbdr)

p = doc.add_paragraph(style='Title'); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(8)
ppr = p._p.get_or_add_pPr(); pbdr = ppr.find(qn('w:pBdr'))
if pbdr is not None: ppr.remove(pbdr)
p.add_run('Bài tập 1  Lịch tuần tự và khả tuần tự').font.color.rgb = RGBColor(0,0,0)
p2 = doc.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER; p2.paragraph_format.space_after = Pt(12)
r = p2.add_run('Môn Hệ quản trị cơ sở dữ liệu'); r.italic = True; r.font.size = Pt(11); r.font.color.rgb = RGBColor(89,89,89)

add_body(doc, 'Giả sử trước khi thực hiện S1 và S2: A = B = 25. Ký hiệu R(X, biến) là đọc X vào biến tạm; W(X, biến) là ghi giá trị biến tạm vào X.')

add_heading(doc, '1. Lịch S1', 1)
add_body(doc, 'S1 là lịch tuần tự trong đó T1 hoàn tất trước khi T2 bắt đầu. Bảng dưới đây trình bày theo các cột T1, T2 và T3; T3 không xuất hiện trong đề bài.')

t = doc.add_table(rows=1, cols=4)
hdr = ['Bước', 'T1', 'T2', 'T3']
for i, x in enumerate(hdr): t.rows[0].cells[i].text = x
rows = [
    ('1', 'Read(A, t)', '', '—'), ('2', 't := t + 100', '', '—'), ('3', 'Write(A, t)', '', '—'),
    ('4', 'Read(B, t)', '', '—'), ('5', 't := t + 100', '', '—'), ('6', 'Write(B, t)', '', '—'),
    ('7', '', 'Read(A, s)', '—'), ('8', '', 's := s × 1', '—'), ('9', '', 'Write(A, s)', '—'),
    ('10', '', 'Read(B, s)', '—'), ('11', '', 's := s × 1', '—'), ('12', '', 'Write(B, s)', '—'),
]
for row in rows:
    cells = t.add_row().cells
    for i, x in enumerate(row): cells[i].text = x
style_table(t); set_widths(t, [0.55, 2.25, 2.25, 0.55])
for row in t.rows[1:]:
    row.cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    row.cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

add_heading(doc, '2. Lịch S2', 1)
add_body(doc, 'S2 được thực hiện theo thứ tự: T1 xử lý A, sau đó T2 xử lý A và B, cuối cùng T1 xử lý B.')
t2 = doc.add_table(rows=1, cols=4)
for i, x in enumerate(hdr): t2.rows[0].cells[i].text = x
rows2 = [
    ('1', 'Read(A, t)', '', '—'), ('2', 't := t + 100', '', '—'), ('3', 'Write(A, t)', '', '—'),
    ('4', '', 'Read(A, s)', '—'), ('5', '', 's := s × 1', '—'), ('6', '', 'Write(A, s)', '—'),
    ('7', '', 'Read(B, s)', '—'), ('8', '', 's := s × 1', '—'), ('9', '', 'Write(B, s)', '—'),
    ('10', 'Read(B, t)', '','—'), ('11', 't := t + 100', '','—'), ('12', 'Write(B, t)', '','—'),
]
for row in rows2:
    cells = t2.add_row().cells
    for i, x in enumerate(row): cells[i].text = x
style_table(t2); set_widths(t2, [0.55, 2.25, 2.25, 0.55])
for row in t2.rows[1:]:
    row.cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER; row.cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

add_heading(doc, '3. Các chu trình và quan hệ trước sau', 1)
add_body(doc, 'Các thao tác của từng giao tác:')
add_body(doc, '• T1: R(A, t) → t := t + 100 → W(A, t) → R(B, t) → t := t + 100 → W(B, t).')
add_body(doc, '• T2: R(A, s) → s := s × 1 → W(A, s) → R(B, s) → s := s × 1 → W(B, s).')
add_body(doc, '• T3: không có thao tác trong bài này.')
add_body(doc, 'Các cạnh xung đột trong S2:')
add_body(doc, '• Trên A: W1(A) xảy ra trước R2(A) và W2(A), nên có cạnh T1 → T2.')
add_body(doc, '• Trên B: W2(B) xảy ra trước R1(B) và W1(B), nên có cạnh T2 → T1.')
add_body(doc, 'Chu trình đi qua các giao tác là: T1 → T2 → T1. Vì đồ thị phụ thuộc có chu trình nên S2 không khả tuần tự theo xung đột và không tương đương với lịch tuần tự S1.')

add_heading(doc, '4. Tính giá trị cuối cùng', 1)
calc = doc.add_table(rows=1, cols=4)
for i, x in enumerate(['Đại lượng', 'Sau T1 trên A', 'Sau T2 trên A và B', 'Sau T1 trên B']): calc.rows[0].cells[i].text = x
for row in [
    ('A', 'A = 25 + 100 = 125', 'A = 125 × 1 = 125', 'A = 125'),
    ('B', 'B vẫn = 25', 'B = 25 × 1 = 25', 'B = 25 + 100 = 125'),
]:
    cells = calc.add_row().cells
    for i, x in enumerate(row): cells[i].text = x
style_table(calc); set_widths(calc, [0.75, 1.75, 2.35, 1.55])
calc.rows[1].cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER; calc.rows[2].cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

add_body(doc, 'Kết luận: S2 không khả tuần tự với S1 theo quan hệ xung đột, nhưng sau khi thực hiện xong vẫn thu được A = 125 và B = 125.')

doc.core_properties.title = 'Bài tập 1 - Lịch tuần tự và khả tuần tự'
doc.core_properties.subject = 'Hệ quản trị cơ sở dữ liệu'
doc.core_properties.author = ' '
doc.save(OUT)
print(OUT)
