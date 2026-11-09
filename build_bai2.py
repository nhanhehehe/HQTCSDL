from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

OUT = 'Bai_2_lich_giao_tac.docx'

def remove_borders(style):
    ppr = style._element.get_or_add_pPr()
    pbdr = ppr.find(qn('w:pBdr'))
    if pbdr is not None: ppr.remove(pbdr)

def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style=f'Heading {level}')
    p.paragraph_format.space_before = Pt(11 if level == 1 else 7)
    p.paragraph_format.space_after = Pt(4)
    p.add_run(text)
    return p

def add_body(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.1
    p.add_run(text)
    return p

def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style='List Bullet' if level == 0 else 'List Bullet 2')
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.05
    p.add_run(text)
    return p

def add_schedule_table(doc, operations, cols=4):
    table = doc.add_table(rows=1, cols=cols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    headers = ['Bước'] + [f'T{i}' for i in range(1, cols)]
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, (tx, op) in enumerate(operations, 1):
        cells = table.add_row().cells
        cells[0].text = str(idx)
        for i in range(1, cols): cells[i].text = op if tx == i else ''
    for row in table.rows:
        for j, cell in enumerate(row.cells):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(0); p.paragraph_format.line_spacing = 1.0
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER if j == 0 else WD_ALIGN_PARAGRAPH.LEFT
                for run in p.runs:
                    run.font.name = 'Aptos'; run.font.size = Pt(10); run.bold = (row is table.rows[0])
            tcPr = cell._tc.get_or_add_tcPr()
            borders = tcPr.first_child_found_in('w:tcBorders')
            if borders is None:
                borders = OxmlElement('w:tcBorders'); tcPr.append(borders)
            for edge in ('top','left','bottom','right','insideH','insideV'):
                el = borders.find(qn('w:'+edge))
                if el is None: el = OxmlElement('w:'+edge); borders.append(el)
                el.set(qn('w:val'), 'single'); el.set(qn('w:sz'), '6'); el.set(qn('w:space'), '0'); el.set(qn('w:color'), '808080')
    return table

doc = Document()
sec = doc.sections[0]
sec.top_margin = Inches(0.7); sec.bottom_margin = Inches(0.7); sec.left_margin = Inches(0.8); sec.right_margin = Inches(0.8)
styles = doc.styles
styles['Normal'].font.name = 'Aptos'; styles['Normal'].font.size = Pt(11)
for nm, size in [('Title', 18), ('Heading 1', 14), ('Heading 2', 12)]:
    styles[nm].font.name = 'Aptos Display'; styles[nm].font.size = Pt(size); styles[nm].font.bold = True; styles[nm].font.color.rgb = RGBColor(0,0,0); remove_borders(styles[nm])

p = doc.add_paragraph(style='Title'); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(7); remove_borders(styles['Title'])
p.add_run('Bài tập 2  Kiểm tra khả tuần tự xung đột')
p2 = doc.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER; p2.paragraph_format.space_after = Pt(12)
r = p2.add_run('Hệ quản trị cơ sở dữ liệu'); r.italic = True; r.font.size = Pt(11); r.font.color.rgb = RGBColor(89,89,89)

add_body(doc, 'Yêu cầu: Với mỗi lịch giao tác S, kiểm tra lịch có khả tuần tự xung đột hay không. Nếu có, liệt kê thứ tự các giao tác trong những lịch tuần tự tương đương.')

add_heading(doc, 'Câu 1', 1)
add_body(doc, 'Lịch giao tác:')
add_schedule_table(doc, [(1,'w1(A)'), (2,'r2(A)'), (3,'r3(A)'), (4,'w4(A)')], cols=5)
add_heading(doc, 'Phân tích', 2)
for x in [
    'Trên A, w1(A) xảy ra trước r2(A) và r3(A), nên có các cạnh T1 → T2 và T1 → T3.',
    'Trên A, w1(A) xảy ra trước w4(A), r2(A) và r3(A) xảy ra trước w4(A), nên có thêm các cạnh T1 → T4, T2 → T4 và T3 → T4.',
    'Đồ thị phụ thuộc không có chu trình, nên lịch là khả tuần tự xung đột.',
    'Các thứ tự giao tác tương đương: T1 → T2 → T3 → T4 và T1 → T3 → T2 → T4.'
]: add_bullet(doc, x)

add_heading(doc, 'Câu 2', 1)
add_body(doc, 'Lịch giao tác:')
add_schedule_table(doc, [(3,'w3(A)'), (2,'w2(C)'), (1,'r1(A)'), (1,'w1(B)'), (1,'r1(C)'), (2,'w2(A)'), (4,'r4(A)'), (4,'w4(D)')], cols=5)
add_heading(doc, 'Phân tích', 2)
for x in [
    'Trên A, w3(A) xảy ra trước r1(A), w2(A) và r4(A), nên có các cạnh T3 → T1, T3 → T2 và T3 → T4.',
    'Trên C, w2(C) xảy ra trước r1(C), nên có cạnh T2 → T1.',
    'Trên A, r1(A) xảy ra trước w2(A), nên có cạnh T1 → T2.',
    'Đồ thị có chu trình T1 → T2 → T1.',
    'Kết luận: lịch không khả tuần tự xung đột và không có lịch tuần tự tương đương.'
]: add_bullet(doc, x)

add_heading(doc, 'Câu 3', 1)
add_body(doc, 'Lịch giao tác:')
add_schedule_table(doc, [(2,'R2(Z)'), (2,'R2(Y)'), (2,'W2(Y)'), (3,'R3(Z)'), (3,'W3(Z)'), (1,'R1(Z)'), (1,'W1(Z)'), (2,'R2(X)'), (2,'W2(X)'), (3,'R3(Y)'), (3,'W3(Y)'), (1,'R1(X)'), (1,'W1(X)'), (1,'R1(Y)'), (1,'W1(Y)')])
add_heading(doc, 'Phân tích', 2)
for x in [
    'Trên Z và Y, các xung đột tạo ra các cạnh T2 → T3, T2 → T1 và T3 → T1.',
    'Trên X, W2(X) xảy ra trước R1(X) và W1(X), nên có thêm cạnh T2 → T1.',
    'Đồ thị phụ thuộc có dạng T2 → T3 → T1 và không có chu trình.',
    'Kết luận: lịch là khả tuần tự xung đột.',
    'Thứ tự giao tác trong lịch tuần tự tương đương: T2 → T3 → T1.'
]: add_bullet(doc, x)

add_heading(doc, 'Tóm tắt kết quả', 1)
for x in ['Câu 1: Khả tuần tự; các thứ tự tương đương là T1 → T2 → T3 → T4 và T1 → T3 → T2 → T4.', 'Câu 2: Không khả tuần tự do có chu trình T1 → T2 → T1.', 'Câu 3: Khả tuần tự; thứ tự tương đương là T2 → T3 → T1.']:
    add_bullet(doc, x)

doc.core_properties.title = 'Bài tập 2 - Kiểm tra khả tuần tự xung đột'
doc.core_properties.subject = 'Hệ quản trị cơ sở dữ liệu'
doc.core_properties.author = ' '
doc.save(OUT)
print(OUT)
