from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import io

def generate_daily_lem_pdf(data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    # Custom styles
    title_style = styles["Heading1"]
    title_style.alignment = 1  # Center alignment if needed, or keep left
    
    normal_style = styles["Normal"]
    
    elements = []

    # -------------------------
    # HEADER
    # -------------------------
    
    project_name = data.get("project_name", "")
    date_str = data.get("date", "")
    steward = data.get("steward", "")
    
    # Title
    title_style = styles["Heading1"]
    title_style.alignment = 1 # Center
    elements.append(Paragraph(f"<b>Daily LEM Report</b>", title_style))
    elements.append(Spacer(1, 10))

    # Header Box
    # We need a table with 1 row and 3 columns.
    # Col 1: Logo
    # Col 2: Project Name / Reporting Date
    # Col 3: Created by
    
    # Column 1 Content: Logo
    # Assuming the logo is at `reports/static/reports/images/logo.png`
    import os
    # Try to find the logo relative to this file or use absolute path if manageable in django
    # For now, let's assume standard django structure or absolute path we just copied
    # The command copied to reports\static\reports\images\logo.png, let's try to resolve that.
    
    logo_path = os.path.join("reports", "static", "reports", "images", "logo.png")
    
    col1_content = []
    if os.path.exists(logo_path):
        # Constrain width to fit in 100px col, keep aspect ratio
        img = Image(logo_path, width=1.2*inch, height=1.2*inch, kind='proportional')
        col1_content.append(img)
    else:
        # Fallback if image not found
        col1_content = [
            Paragraph("<b>STAMSH</b>", styles["Normal"]),
            Spacer(1, 6),
            Paragraph("<b>TEMIXW</b>", styles["Normal"]),
        ]
    
    # Column 2 Content
    col2_content = [
        Paragraph(f"<b>Project Name:</b> {project_name}", styles["Normal"]),
        Spacer(1, 6),
        Paragraph(f"<b>Reporting Date:</b> {date_str}", styles["Normal"]),
    ]
    
    # Column 3 Content
    col3_content = [
        Paragraph(f"<b>Created by:</b> {steward}", styles["Normal"]),
    ]
    
    header_data = [[col1_content, col2_content, col3_content]]
    
    # Total width approx 732. Let's split meaningfuly.
    # Col 1: 100, Col 2: 350, Col 3: 280
    header_table = Table(header_data, colWidths=[100, 350, 282])
    
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        # Center align the text in the first column if desired, screenshot looks centered
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
    ]))
    
    elements.append(header_table)
    elements.append(Spacer(1, 20))

    # -------------------------
    # MAIN TABLE
    # -------------------------
    # Columns: Name, Role, Start time, End time, Total hours, Equipment/Extras
    
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

    # Add some blank rows if needed, or just let it be dynamic
    if not rows:
        table_data.append(["No entries found", "", "", "", "", ""])

    # Column widths calculation - total width for landscape is about 11 inches (792 pts) minus margins (60) = 732 pts
    # Let's distribute roughly:
    # Name: 150, Role: 120, Start: 60, End: 60, Total: 60, Extras: 280
    col_widths = [150, 120, 70, 70, 70, 250]

    main_table = Table(table_data, colWidths=col_widths)
    
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.white), # Header background
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), # Header font
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black), # Grid lines
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    
    # Customizing columns alignment
    # Center align Start, End, Total Hours
    table_style.append(('ALIGN', (2, 0), (4, -1), 'CENTER'))

    main_table.setStyle(TableStyle(table_style))
    
    elements.append(main_table)
    elements.append(Spacer(1, 30))

    # -------------------------
    # FOOTER / ROLES LEGEND
    # -------------------------
    # The screenshot shows "Roles: LCT, TCP..."
    # We can add this if we have the data, for now just a placeholder or based on data.
    
    # Adding a place for signatures if typical for LEM
    # elements.append(Paragraph("<b>Signatures:</b> __________________________", styles["Normal"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer
