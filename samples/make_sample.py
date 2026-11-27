"""Generate a fictional 3-page policy PDF used by the demo and tests."""
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

PAGES = [
    ("Fictional Finance Co. Expense Policy", [
        "This policy applies to all employees of Fictional Finance Co. and is effective from 1 April 2026.",
        "Employees may claim travel expenses incurred for approved business trips. Claims must be submitted within 30 days of the trip end date.",
        "Economy class is required for flights shorter than six hours. Business class needs written approval from a director."]),
    ("Approval Limits", [
        "Expenses up to Rs 5,000 are approved by the line manager. Expenses between Rs 5,001 and Rs 50,000 need department head approval.",
        "Any expense above Rs 50,000 must be approved by the finance director before payment is made.",
        "Cash advances are capped at Rs 20,000 per employee and must be settled within 15 days."]),
    ("Reimbursement and Audit", [
        "Approved claims are reimbursed to the employee bank account within 10 working days.",
        "Receipts are mandatory for every claim above Rs 500. Missing receipts delay reimbursement until an affidavit is filed.",
        "The internal audit team reviews a random 5 percent sample of claims every quarter."]),
]

def build(path):
    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    flow = []
    for i, (title, paras) in enumerate(PAGES):
        flow.append(Paragraph(title, st["Title"]))
        for p in paras:
            flow += [Paragraph(p, st["BodyText"]), Spacer(1, 10)]
        if i < len(PAGES) - 1:
            flow.append(PageBreak())
    doc.build(flow)

if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "samples/expense_policy.pdf")
