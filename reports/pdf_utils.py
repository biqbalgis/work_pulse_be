from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter,A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import io

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
    steward = data.get("steward", "")
    logo_path = os.path.join("reports", "static", "reports", "images", "logo.png")

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
            Paragraph(f"<b>Created by:</b> {steward}", styles["Normal"]),
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
        
        # --- Draw Footer (Page Number) ---
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
