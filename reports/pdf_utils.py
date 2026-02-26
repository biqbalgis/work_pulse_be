from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter,A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import io
import base64
from reportlab.lib.utils import ImageReader

def generate_daily_lem_pdf(data):
    # Prepare Buffer
    buffer = io.BytesIO()
    
    # Define Layout
    # Increase top margin to make room for the repeating header
    # Header approx height: Title (20) + Spacer (10) + Box (60) ~ 90-100 pts. 
    # Let's set topMargin to 130 to be safe.
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=150, 
        bottomMargin=40 
    )

    styles = getSampleStyleSheet()

    # ---------------------------------------------------------
    # Helper Components (for Callback)
    # ---------------------------------------------------------
    import os
    
    # 1. Prepare Header Content
    project_name = data.get("project_name", "")
    date_str = data.get("date", "")
    lem_number = data.get("lem_number", "")
    logo_path = os.path.join("reports", "static", "reports", "images", "logo.png")

    sign_data = data.get("sign", None)
    sign_image = None
    if sign_data and sign_data.startswith("data:image"):
        try:
            head, base64_str = sign_data.split(",", 1)
            image_bytes = base64.b64decode(base64_str)
            sign_image = ImageReader(io.BytesIO(image_bytes))
        except Exception:
            pass

    def draw_header_footer(canvas, doc):
        canvas.saveState()
        
        # --- Draw Title ---
        title_style = styles["Heading1"]
        title_style.alignment = 1 # Center
        title = Paragraph(f"<b>Daily LEM Report</b>", title_style)
        w, h = title.wrap(doc.width, doc.topMargin)
        # Position title at the very top of the margin
        title.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h )
        
        # --- Draw Header Box ---
        # Same logic as before, but wrapped in a function
        col1_content = []
        if os.path.exists(logo_path):
            img = Image(logo_path, width=1.2*inch, height=1.2*inch, kind='proportional')
            col1_content.append(img)
        else:
            col1_content = [
                Paragraph("<b>STAMSH</b>", styles["Normal"]),
                Spacer(1, 6),
                Paragraph("<b>TEMIXW</b>", styles["Normal"]),
            ]
        
        col2_content = [
            Paragraph(f"<b>Project Name:</b> {project_name}", styles["Normal"]),
            Spacer(1, 6),
            Paragraph(f"<b>Reporting Date:</b> {date_str}", styles["Normal"]),
        ]
        
        col3_content = [
            Paragraph(f"<font size=14><b>LEM #:</b> {lem_number}</font>", styles["Normal"]),
        ]
        
        header_data = [[col1_content, col2_content, col3_content]]
        # Approx 732 width available on Letter Landscape, A4 Landscape is wider (~780 printable)
        # A4 Width = 842pts. Margins 30+30=60. Printable = 782.
        # Let's widen the columns slightly: 100, 400, 282
        header_table = Table(header_data, colWidths=[100, 400, 282])
        
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ]))
        
        # Draw Table below Title
        # Title y was approx (height + topMargin - h). 
        # Let's say title takes ~20pts space.
        w_t, h_t = header_table.wrap(doc.width, doc.topMargin)
        header_table.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h - 15 - h_t)
        
        # --- Draw Footer (Signature Section) ---
        # Draw signature line first
        line_start_x = doc.leftMargin
        line_end_x = doc.leftMargin + 250  # 250 points wide line
        line_y = 50
        
        if sign_image:
            canvas.drawImage(sign_image, line_start_x, line_y + 2, width=250, height=40, preserveAspectRatio=True, anchor='sw', mask='auto')
            
        canvas.line(line_start_x, line_y, line_end_x, line_y)
        
        # Draw "Name & Signature" label below the line
        canvas.setFont("Helvetica", 10)
        canvas.drawString(doc.leftMargin, 38, "Name & Signature")
        
        # Page number below signature
        page_num_text = f"Page {doc.page}"
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(A4[1]/2.0, 20, page_num_text)
        
        canvas.restoreState()

    # -------------------------
    # MAIN CONTENT (Flowables)
    # -------------------------
    elements = []
    
    # Just the Table now, no static header in elements
    
    table_headers = ["Name", "Role", "Start Time", "End Time", "Total Hours", "Equipment/Extras"]
    table_data = [table_headers]
    
    rows = data.get("rows", [])
    
    for row in rows:
        table_data.append([
            row.get("name", ""),
            row.get("role", ""),
            row.get("start", ""),
            row.get("end", ""),
            row.get("hours", ""),
            row.get("extras", "")
        ])

    if not rows:
        table_data.append(["No entries found", "", "", "", "", ""])

    # Columns widths for A4 Landscape (Printable ~782)
    # 150 + 120 + 70 + 70 + 70 + 300 = 780
    col_widths = [150, 120, 70, 70, 70, 300]

    main_table = Table(table_data, colWidths=col_widths, repeatRows=1) # Repeat Header Row
    
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
        ('ALIGN', (2, 0), (4, -1), 'CENTER'),
    ]

    main_table.setStyle(TableStyle(table_style))
    elements.append(main_table)

    # Build PDF with callbacks
    doc.build(elements, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    
    buffer.seek(0)
    return buffer

def generate_costing_lem_pdf(data):
    # Prepare Buffer
    buffer = io.BytesIO()
    
    # Define Layout
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=150, 
        bottomMargin=40 
    )

    styles = getSampleStyleSheet()

    # ---------------------------------------------------------
    # Helper Components (for Callback)
    # ---------------------------------------------------------
    import os
    
    # 1. Prepare Header Content
    project_name = data.get("project_name", "")
    date_str = data.get("date", "")
    lem_number = data.get("lem_number", "")
    logo_path = os.path.join("reports", "static", "reports", "images", "logo.png")

    sign_data = data.get("sign", None)
    sign_image = None
    if sign_data and sign_data.startswith("data:image"):
        try:
            head, base64_str = sign_data.split(",", 1)
            image_bytes = base64.b64decode(base64_str)
            sign_image = ImageReader(io.BytesIO(image_bytes))
        except Exception:
            pass

    def draw_heading_footer(canvas, doc):
        canvas.saveState()
        
        # --- Draw Title ---
        title_style = styles["Heading1"]
        title_style.alignment = 1 # Center
        title = Paragraph(f"<b>Daily LEM Costing Report</b>", title_style)
        w, h = title.wrap(doc.width, doc.topMargin)
        title.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h )
        
        # --- Draw Header Box ---
        col1_content = []
        if os.path.exists(logo_path):
            img = Image(logo_path, width=1.2*inch, height=1.2*inch, kind='proportional')
            col1_content.append(img)
        else:
            col1_content = [
                Paragraph("<b>STAMSH</b>", styles["Normal"]),
                Spacer(1, 6),
                Paragraph("<b>TEMIXW</b>", styles["Normal"]),
            ]
        
        col2_content = [
            Paragraph(f"<b>Project Name:</b> {project_name}", styles["Normal"]),
            Spacer(1, 6),
            Paragraph(f"<b>Reporting Date:</b> {date_str}", styles["Normal"]),
        ]
        
        col3_content = [
            Paragraph(f"<b>LEM #:</b> {lem_number}", styles["Normal"]),
        ]
        
        header_data = [[col1_content, col2_content, col3_content]]
        # A4 Landscape Printable ~782
        header_table = Table(header_data, colWidths=[100, 400, 282])
        
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ]))
        
        w_t, h_t = header_table.wrap(doc.width, doc.topMargin)
        header_table.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h - 15 - h_t)
        
        # --- Draw Footer (Signature Section) ---
        # Draw signature line first
        line_start_x = doc.leftMargin
        line_end_x = doc.leftMargin + 250  # 250 points wide line
        line_y = 50
        
        if sign_image:
            canvas.drawImage(sign_image, line_start_x, line_y + 2, width=250, height=40, preserveAspectRatio=True, anchor='sw', mask='auto')
            
        canvas.line(line_start_x, line_y, line_end_x, line_y)
        
        # Draw "Name & Signature" label below the line
        canvas.setFont("Helvetica", 10)
        canvas.drawString(doc.leftMargin, 38, "Name & Signature")
        
        # Page number below signature
        page_num_text = f"Page {doc.page}"
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(A4[1]/2.0, 20, page_num_text)
        
        canvas.restoreState()

    # -------------------------
    # MAIN CONTENT 
    # -------------------------
    elements = []
    
    # Columns: Employee Name, Job Title, Regular Hours/Rate, Over Time Hours/Rate, Double Time Hours/Rate, Total Cost
    table_headers = ["Employee Name", "Job Title", "Reg Hrs", "Reg Rate", "OT Hrs", "OT Rate", "DT Hrs", "DT Rate", "Total Cost"]
    table_data = [table_headers]
    
    rows = data.get("rows", [])
    
    total_cost_sum = 0.0
    
    for row in rows:
        c = row.get("total_cost", 0)
        total_cost_sum += float(c)
        table_data.append([
            row.get("employee_name", ""),
            row.get("job_title", ""),
            str(row.get("regular_hours", 0)),
            f"${row.get('regular_rate', 0)}",
            str(row.get("overtime_hours", 0)),
            f"${row.get('overtime_rate', 0)}",
            str(row.get("double_time_hours", 0)),
            f"${row.get('double_time_rate', 0)}",
            f"${c}"
        ])

    if not rows:
        table_data.append(["No entries found", "", "", "", "", "", "", "", ""])

    # Add Total Row (Spanning 8 columns for "TOTAL")
    table_data.append([
        Paragraph("<b>TOTAL</b>", styles["Normal"]), 
        "", "", "", "", "", "", "", 
        Paragraph(f"<b>${round(total_cost_sum, 2)}</b>", styles["Normal"])
    ])

    # Columns widths for A4 Landscape (Printable ~782)
    # Employee(120), Title(100), 6 columns for Hrs/Rate (60/65 each), Total Cost(187)
    col_widths = [120, 100, 60, 65, 60, 65, 60, 65, 187] 

    main_table = Table(table_data, colWidths=col_widths, repeatRows=1) 
    
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # Center headers
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),   # Left align values
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        # Bold Total Row
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        # Center align the numeric columns (indices 2 to 7)
        ('ALIGN', (2, 0), (7, -1), 'CENTER'),
        # Span columns for TOTAL label and center it
        ('SPAN', (0, -1), (7, -1)),
        ('ALIGN', (0, -1), (7, -1), 'CENTER'),
        # Center the total cost value
        ('ALIGN', (8, -1), (8, -1), 'CENTER'),
    ]

    main_table.setStyle(TableStyle(table_style))
    elements.append(main_table)

    doc.build(elements, onFirstPage=draw_heading_footer, onLaterPages=draw_heading_footer)
    
    buffer.seek(0)
    return buffer
