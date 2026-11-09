"""Withholding tax on the amount excluding VAT (requirement C).

The requirement's own worked example ("Invoice Total: GHS 12,000.00 ...
Net Payment to Supplier: GHS 11,250.00") withholds on the invoice's NET
amount, not its VAT-inclusive total. A Payment Entry settling that invoice
already carries the invoice's own VAT breakdown - nothing needs to be typed
in manually to use it.

One checkbox, **Include VAT**, decides which base this Payment Entry
withholds on:

* **Unticked (default)** - VAT is excluded from the base. For any amount
  allocated against an Invoice or Order, the VAT-excluded base is read
  straight from that document's own ``net_total`` / ``grand_total`` ratio -
  no manual rate, it comes from the real document, prorated for a partial
  allocation. An invoice that already withheld tax on itself (``apply_tds``
  ticked there - requirement C line 10, which needs no code, ERPNext gets it
  right by itself) is excluded from this payment's base entirely, so the
  same transaction is never withheld twice. The unallocated portion (a
  genuine advance, with no document to read VAT from) is used as-is.
* **Ticked** - standard ERPNext withholding, unmodified: the base is
  whatever ``PaymentTaxWithholding._update_taxable_amounts`` computes out of
  the box (unallocated amount plus anything allocated against a Sales/
  Purchase Order), VAT included. Only tick this for a payment that genuinely
  should not have VAT excluded.
"""

import frappe
from frappe.utils import flt

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import (
	PaymentTaxWithholding,
)


class PrymagePaymentTaxWithholding(PaymentTaxWithholding):
	"""Same engine; only the VAT-excluded base path replaces the base totals.

	When "Include VAT" is ticked, ``super()._update_taxable_amounts()`` runs
	untouched - this class only takes over to exclude VAT.
	"""

	def _update_taxable_amounts(self):
		if self.doc.get("custom_payment_includes_vat"):
			super()._update_taxable_amounts()
			category = next(iter(self.category_details.values()), None)
			exchange_rate = self.get_conversion_rate() or 1
			self.doc.custom_wht_base_amount = flt(
				flt((category or {}).get("taxable_amount", 0)) / exchange_rate,
				self.doc.precision("paid_amount"),
			)
			return

		category = next(iter(self.category_details.values()), None)
		if not category:
			return

		net_of_vat = flt(self.doc.unallocated_amount)
		for row in self.doc.references:
			if _reference_already_withheld(row.reference_doctype, row.reference_name):
				continue
			net_of_vat += flt(row.allocated_amount) * _net_of_tax_fraction(
				row.reference_doctype, row.reference_name
			)

		net_of_vat = flt(net_of_vat, self.precision)
		exchange_rate = self.get_conversion_rate() or 1

		category["taxable_amount"] = flt(net_of_vat * exchange_rate, self.precision)
		# Shown read-only on the form so the figure can be checked without
		# reverse-engineering it from the tax row.
		self.doc.custom_wht_base_amount = flt(net_of_vat, self.doc.precision("paid_amount"))


def _reference_already_withheld(reference_doctype: str, reference_name: str) -> bool:
	"""Did this reference's own document already withhold tax on itself?

	Only an Invoice can - an Order is never accounting-posted and never has an
	``apply_tds`` of its own, so it is never excluded here.
	"""
	if not frappe.get_meta(reference_doctype).has_field("apply_tds"):
		return False

	return bool(frappe.db.get_value(reference_doctype, reference_name, "apply_tds"))


def _net_of_tax_fraction(reference_doctype: str, reference_name: str) -> float:
	"""What fraction of this document's grand total is actually net of tax?

	Reads the real ``net_total`` / ``grand_total`` off the document being
	settled - the same two figures ERPNext's own invoice-level withholding
	reads from, just expressed as a ratio so it can be applied to however much
	of it *this* payment allocates. Falls back to 1.0 (no adjustment) for a
	reference type that does not carry these fields, rather than guessing.
	"""
	meta = frappe.get_meta(reference_doctype)
	if not (meta.has_field("net_total") and meta.has_field("grand_total")):
		return 1.0

	net_total, grand_total = frappe.db.get_value(
		reference_doctype, reference_name, ["net_total", "grand_total"]
	)
	if not grand_total:
		return 1.0

	# Clamped: a discount or deduction-type tax row could in principle push
	# grand_total below net_total, which would otherwise read as "more than
	# 100% is tax" - nonsensical as an exclusion fraction.
	return min(max(flt(net_total) / flt(grand_total), 0.0), 1.0)


class PrymagePaymentEntryExtension:
	"""Re-runs the withholding calculation with VAT excluded from the base.

	Registered through ``extend_doctype_class``, so this is a mixin placed in
	front of whatever Payment Entry controller is in force - hrms overrides it
	on this bench - and ``super().validate()`` continues down that chain.

	``PaymentEntry.validate()`` calls ``PaymentTaxWithholding(self).on_validate()``
	inline, so there is no hook to swap the controller and overriding
	``validate()`` wholesale would mean copying 25 lines of ERPNext that change
	between versions. Instead the standard pass runs first and ours replaces it.

	That is safe because the controller is idempotent:
	``on_validate()`` -> ``_is_tax_withholding_applicable()`` zeroes the existing
	withholding tax rows, ``calculate()`` rebuilds ``tax_withholding_entries``
	from scratch and ``update_tax_rows()`` -> ``apply_taxes()`` recomputes the
	totals. The second pass replaces the first rather than adding to it.
	"""

	def validate(self):
		super().validate()

		if not self.apply_tds or not self.tax_withholding_category:
			self.custom_wht_base_amount = 0
			return

		PrymagePaymentTaxWithholding(self).on_validate()
