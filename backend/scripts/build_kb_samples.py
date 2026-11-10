"""Rebuild the sample files offered under Import knowledge (frontend/public/guides/templates).

    pip install fpdf2 python-docx openpyxl matplotlib      # matplotlib only for its DejaVu fonts
    python backend/scripts/build_kb_samples.py             # writes kb-template-{en,ka}.*

One content set per language, written out in every format the importer reads, so a customer can
download the format they have and see the shape the NON-AI import cuts well (`kb_ingest`:
blank line = topic boundary in text files, one row = one entry in CSV, one line per paragraph or
row in PDF/DOCX/XLSX, about 1,000 characters per chunk). The samples carry no instructions
inside them: they are importable as they are, and the explanation lives in the Import panel.
`backend/tests/test_kb_samples.py` runs every file through the real extractor and chunker.
"""
import csv
import io
import os
import sys

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "public", "guides", "templates")

CONTENT = {
    "en": {
        "title": "Support policies (sample)",
        "topics": [
            ("Support hours",
             "Support is available 24/7 by phone and chat. Email tickets are answered within 4 business hours. "
             "Outside business hours, critical issues are handled by the on-call team."),
            ("Refund policy",
             "Refunds are available within 14 days of purchase. The customer contacts support with the order "
             "number; processing takes 3–5 business days. Partial refunds are possible for unused subscription months."),
            ("Service plans",
             "Basic includes up to 10 users. Business includes up to 50 users and priority support. Enterprise "
             "includes unlimited users, a dedicated manager and a service-level agreement. Yearly billing saves 20%."),
            ("Escalating an urgent issue",
             "A customer can say \"urgent escalation\" to any agent, or mark a ticket Critical. A supervisor replies "
             "within 30 minutes. Escalations are never closed without customer confirmation."),
            ("Data storage",
             "All customer data is stored in EU data centres and never leaves the region."),
            ("Complaints",
             "A complaint can be submitted by phone, chat or email. It is registered within one business day and "
             "answered in writing within 10 business days. The customer is told the reference number at once."),
            ("Changing contact details",
             "Contact details can be changed in the customer portal, or by asking an agent after identity "
             "verification. The change takes effect immediately and a confirmation is sent to the old and new address."),
            ("Public holidays",
             "On public holidays support works from 10:00 to 18:00. Critical issues are still handled around the clock "
             "by the on-call team. Email tickets received on a holiday are answered on the next business day."),
        ],
        "qa_head": ("Question", "Answer"),
        "qa": [
            ("What are your support hours?",
             "Support is available 24/7 by phone and chat. Email tickets are answered within 4 business hours."),
            ("How do I request a refund?",
             "Refunds are available within 14 days of purchase. Contact support with your order number; processing takes 3–5 business days."),
            ("Which service plans do you offer?",
             "Basic (10 users), Business (50 users, priority support) and Enterprise (unlimited users, dedicated manager). Yearly billing saves 20%."),
            ("How do I escalate an urgent issue?",
             "Say \"urgent escalation\" to any agent or mark the ticket Critical; a supervisor replies within 30 minutes."),
            ("Where is my data stored?",
             "All customer data is stored in EU data centres and never leaves the region."),
            ("How do I make a complaint?",
             "By phone, chat or email. It is registered within one business day and answered in writing within 10 business days."),
            ("How do I change my contact details?",
             "In the customer portal, or ask an agent after identity verification; the change takes effect immediately."),
            ("Is support open on public holidays?",
             "Yes, from 10:00 to 18:00; critical issues are handled around the clock."),
        ],
        "table_head": ("Plan", "Users", "Support", "Billing"),
        "table": [
            ("Basic", "Up to 10 users", "Standard support", "Monthly or yearly; yearly saves 20%"),
            ("Business", "Up to 50 users", "Priority support", "Monthly or yearly; yearly saves 20%"),
            ("Enterprise", "Unlimited users", "Dedicated manager and service-level agreement", "Yearly contract"),
        ],
        "sheet_topics": "Policies",
        "sheet_plans": "Plans",
    },
    "ka": {
        "title": "მხარდაჭერის პოლიტიკები (მაგალითი)",
        "topics": [
            ("მხარდაჭერის საათები",
             "მხარდაჭერა ხელმისაწვდომია 24/7 ტელეფონითა და ჩატით. ელფოსტის მოთხოვნებს ვპასუხობთ 4 სამუშაო საათში. "
             "არასამუშაო საათებში კრიტიკულ საკითხებს მორიგე გუნდი განიხილავს."),
            ("თანხის დაბრუნების პოლიტიკა",
             "თანხის დაბრუნება შესაძლებელია შეძენიდან 14 დღეში. კლიენტი მხარდაჭერას მიმართავს შეკვეთის ნომრით; "
             "დამუშავებას 3–5 სამუშაო დღე სჭირდება. გამოუყენებელი თვეების ნაწილობრივი დაბრუნება შესაძლებელია."),
            ("მომსახურების გეგმები",
             "Basic მოიცავს 10 მომხმარებლამდე. Business — 50 მომხმარებლამდე და პრიორიტეტულ მხარდაჭერას. "
             "Enterprise — შეუზღუდავ მომხმარებლებს, პერსონალურ მენეჯერსა და SLA-ს. წლიური გადახდა 20%-ს ზოგავს."),
            ("გადაუდებელი საკითხის ესკალაცია",
             "კლიენტს შეუძლია ნებისმიერ ოპერატორს უთხრას „გადაუდებელი ესკალაცია“ ან მოთხოვნა მონიშნოს კრიტიკულად. "
             "სუპერვაიზერი პასუხობს 30 წუთში. ესკალაცია არ იხურება კლიენტის დადასტურების გარეშე."),
            ("მონაცემთა შენახვა",
             "კლიენტების ყველა მონაცემი ევროკავშირის მონაცემთა ცენტრებში ინახება და რეგიონს არ ტოვებს."),
            ("საჩივრები",
             "საჩივრის გაგზავნა შეიძლება ტელეფონით, ჩატით ან ელფოსტით. ის რეგისტრირდება ერთი სამუშაო დღის განმავლობაში "
             "და წერილობით პასუხს 10 სამუშაო დღეში იღებთ. საცნობარო ნომერს კლიენტს მაშინვე ვაცნობებთ."),
            ("საკონტაქტო მონაცემების შეცვლა",
             "საკონტაქტო მონაცემების შეცვლა შეიძლება მომხმარებლის პორტალში ან პირადობის დადასტურების შემდეგ ოპერატორის დახმარებით. "
             "ცვლილება მაშინვე ძალაში შედის და დადასტურება ძველ და ახალ მისამართზე იგზავნება."),
            ("უქმე დღეები",
             "უქმე დღეებში მხარდაჭერა 10:00-დან 18:00-მდე მუშაობს. კრიტიკულ საკითხებს მორიგე გუნდი მაინც მთელი დღე-ღამის განმავლობაში განიხილავს. "
             "უქმე დღეს მიღებულ ელფოსტის მოთხოვნებს მომდევნო სამუშაო დღეს ვპასუხობთ."),
        ],
        "qa_head": ("კითხვა", "პასუხი"),
        "qa": [
            ("რა საათებში მუშაობს მხარდაჭერა?",
             "მხარდაჭერა ხელმისაწვდომია 24/7 ტელეფონითა და ჩატით. ელფოსტის მოთხოვნებს ვპასუხობთ 4 სამუშაო საათში."),
            ("როგორ დავიბრუნო თანხა?",
             "თანხის დაბრუნება შესაძლებელია შეძენიდან 14 დღეში. მიმართეთ მხარდაჭერას შეკვეთის ნომრით; დამუშავებას 3–5 სამუშაო დღე სჭირდება."),
            ("რა გეგმებს სთავაზობთ?",
             "Basic (10 მომხმარებელი), Business (50 მომხმარებელი, პრიორიტეტული მხარდაჭერა), Enterprise (შეუზღუდავი, პერსონალური მენეჯერი). წლიური გადახდა 20%-ს ზოგავს."),
            ("როგორ მოვახდინო გადაუდებელი ესკალაცია?",
             "უთხარით ოპერატორს „გადაუდებელი ესკალაცია“ ან მონიშნეთ მოთხოვნა კრიტიკულად; სუპერვაიზერი 30 წუთში გიპასუხებთ."),
            ("სად ინახება ჩემი მონაცემები?",
             "კლიენტების ყველა მონაცემი ევროკავშირის მონაცემთა ცენტრებში ინახება და რეგიონს არ ტოვებს."),
            ("როგორ გავგზავნო საჩივარი?",
             "ტელეფონით, ჩატით ან ელფოსტით. ის ერთ სამუშაო დღეში რეგისტრირდება და წერილობით პასუხს 10 სამუშაო დღეში იღებთ."),
            ("როგორ შევცვალო საკონტაქტო მონაცემები?",
             "მომხმარებლის პორტალში ან პირადობის დადასტურების შემდეგ ოპერატორთან; ცვლილება მაშინვე ძალაში შედის."),
            ("მუშაობს თუ არა მხარდაჭერა უქმე დღეებში?",
             "დიახ, 10:00-დან 18:00-მდე; კრიტიკულ საკითხებს მთელი დღე-ღამის განმავლობაში განვიხილავთ."),
        ],
        "table_head": ("გეგმა", "მომხმარებლები", "მხარდაჭერა", "გადახდა"),
        "table": [
            ("Basic", "10 მომხმარებლამდე", "სტანდარტული მხარდაჭერა", "თვიური ან წლიური; წლიური 20%-ს ზოგავს"),
            ("Business", "50 მომხმარებლამდე", "პრიორიტეტული მხარდაჭერა", "თვიური ან წლიური; წლიური 20%-ს ზოგავს"),
            ("Enterprise", "შეუზღუდავი მომხმარებლები", "პერსონალური მენეჯერი და SLA", "წლიური ხელშეკრულება"),
        ],
        "sheet_topics": "პოლიტიკები",
        "sheet_plans": "გეგმები",
    },
}


def write(name: str, data: bytes) -> None:
    path = os.path.join(OUT, name)
    with open(path, "wb") as fh:
        fh.write(data)
    print("wrote", name, len(data))


def md(c) -> bytes:
    # Heading and its text with NO blank line between; a blank line between topics.
    blocks = [f"# {c['title']}"] + [f"## {h}\n{t}" for h, t in c["topics"]]
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def txt(c) -> bytes:
    blocks = [c["title"].upper()] + [f"{h.upper()}\n{t}" for h, t in c["topics"]]
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def csv_bytes(head, rows) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(head)
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")        # BOM so Excel shows Georgian right


def docx(c) -> bytes:
    import docx as D
    from docx.shared import Pt
    d = D.Document()
    d.add_heading(c["title"], level=1)
    for h, t in c["topics"]:
        p = d.add_paragraph()
        r = p.add_run(h + ". ")           # the topic is the first words of EVERY paragraph
        r.bold = True
        p.add_run(t)
    # A table is read row by row as "cell | cell", so each row repeats what it is about.
    d.add_paragraph()
    tbl = d.add_table(rows=1, cols=len(c["table_head"]))
    tbl.style = "Table Grid"
    for i, h in enumerate(c["table_head"]):
        tbl.rows[0].cells[i].text = h
    for row in c["table"]:
        cells = tbl.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v
    for style in ("Normal", "Heading 1"):
        d.styles[style].font.name = "Sylfaen"       # Georgian-capable; Word substitutes if absent
    d.styles["Normal"].font.size = Pt(11)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def xlsx(c) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = c["sheet_topics"]
    ws.append(["Topic" if c is CONTENT["en"] else "თემა", "Rule" if c is CONTENT["en"] else "წესი"])
    for h, t in c["topics"]:
        ws.append([h, t])
    ws2 = wb.create_sheet(c["sheet_plans"])
    ws2.append(list(c["table_head"]))
    for row in c["table"]:
        ws2.append(list(row))
    for sheet, widths in ((ws, (28, 110)), (ws2, (14, 26, 44, 40))):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for i, wdt in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = wdt
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pdf(c) -> bytes:
    import matplotlib
    from fpdf import FPDF
    fonts = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    p = FPDF()
    p.set_auto_page_break(True, 18)
    p.add_font("DejaVu", "", os.path.join(fonts, "DejaVuSans.ttf"))
    p.add_font("DejaVu", "B", os.path.join(fonts, "DejaVuSans-Bold.ttf"))
    p.add_page()
    p.set_font("DejaVu", "B", 16)
    p.multi_cell(0, 9, c["title"], new_x="LMARGIN", new_y="NEXT")
    p.ln(3)
    for h, t in c["topics"]:
        p.set_font("DejaVu", "", 11)
        p.multi_cell(0, 6.5, f"**{h}.** {t}", markdown=True, new_x="LMARGIN", new_y="NEXT")
        p.ln(3)
    p.set_title(c["title"])
    return bytes(p.output())


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    for lang, c in CONTENT.items():
        base = f"kb-template-{lang}"
        write(f"{base}.md", md(c))
        write(f"{base}.txt", txt(c))
        write(f"{base}.csv", csv_bytes(c["qa_head"], c["qa"]))
        write(f"{base}-table.csv", csv_bytes(c["table_head"], c["table"]))
        write(f"{base}.docx", docx(c))
        write(f"{base}.xlsx", xlsx(c))
        write(f"{base}.pdf", pdf(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
