"""Keep invoice-level withholding on the net of VAT (requirement C line 10).

ERPNext computes withholding on ``item.base_net_amount`` - VAT excluded -
unless the Tax Withholding Category is set to ``Gross Total``. Nothing needs
building for the correct behaviour; what is worth having is a warning the moment
somebody configures it the other way, because the resulting figures look
plausible and reconcile against nothing.
"""

import frappe
from frappe import _


def validate_withholding_basis(doc, method=None):
	if not doc.get("apply_tds"):
		return

	categories = {
		row.tax_withholding_category
		for row in doc.get("items", [])
		if row.get("apply_tds") and row.get("tax_withholding_category")
	}
	if not categories:
		return

	gross = frappe.get_all(
		"Tax Withholding Category",
		filters={"name": ("in", list(categories)), "tax_deduction_basis": "Gross Total"},
		pluck="name",
	)
	if not gross:
		return

	frappe.msgprint(
		_(
			"Tax Withholding Category {0} is set to deduct on <b>Gross Total</b>, so the "
			"withholding tax on this invoice includes VAT. Set it to <b>Net Total</b> if the "
			"tax should be calculated on the value of the service only."
		).format(frappe.bold(", ".join(gross))),
		title=_("Withholding Tax Includes VAT"),
		indicator="orange",
	)
