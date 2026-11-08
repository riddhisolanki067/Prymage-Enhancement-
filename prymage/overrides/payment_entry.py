"""Withholding tax on the amount excluding VAT (requirement C).

The requirement's own worked example ("Invoice Total: GHS 12,000.00 ...
Net Payment to Supplier: GHS 11,250.00") is a Payment Entry settling an
invoice. When it does, the invoice being settled *already has its own VAT
broken out* in its own tax table - there is no reason to ask anyone to
manually re-type a VAT rate the system can already see. So:

* **An amount allocated against an Invoice or Order** has its VAT-excluded
  base read straight from that document's own ``net_total`` / ``grand_total``
  ratio. No checkbox, no manual rate - it comes from the real document.
* **The one exception**: if the invoice being settled already withheld tax on
  itself (``apply_tds`` ticked there - requirement C line 10, which needs no
  code, ERPNext gets it right by itself), that allocation is excluded
  entirely, so the same transaction is never withheld twice.
* **The unallocated portion** of the payment - a genuine advance, paid before
  any invoice exists - has no document to read a tax breakdown from. That is
  the *only* case the "Payment Includes VAT" checkbox and its rate/amount
  fields are for.

``PaymentTaxWithholding._update_taxable_amounts`` - verified in source,
``erpnext/accounts/doctype/tax_withholding_entry/tax_withholding_entry.py`` -
only ever counts ``unallocated_amount`` plus amounts allocated to a Sales/
Purchase *Order*, and never looks at tax at all. This module replaces it.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import (
	PaymentTaxWithholding,
)


class PrymagePaymentTaxWithholding(PaymentTaxWithholding):
	"""Same engine; the taxable base is rebuilt entirely rather than inherited.

	Does not call ``super()._update_taxable_amounts()`` - the base
	implementation's own allocation filter is exactly what needs replacing.
	"""

	def _update_taxable_amounts(self):
		category = next(iter(self.category_details.values()), None)
		if not category:
			return

		gross_in_party_currency = 0.0

		for row in self.doc.references:
			if _reference_already_withheld(row.reference_doctype, row.reference_name):
				continue
			net_fraction = _net_of_tax_fraction(row.reference_doctype, row.reference_name)
			gross_in_party_currency += flt(row.allocated_amount) * net_fraction

		# The unallocated leg has no document of its own - this is the only
		# place a manually entered VAT figure is ever used.
		unallocated = flt(self.doc.unallocated_amount)
		if unallocated:
			if self.doc.get("custom_payment_includes_vat"):
				vat_on_unallocated = _vat_component(self.doc, unallocated)
				if vat_on_unallocated > unallocated:
					frappe.throw(
						_(
							"The VAT entered ({0}) is larger than the unallocated amount "
							"({1}) the withholding tax would be calculated on."
						).format(
							frappe.format(vat_on_unallocated, {"fieldtype": "Currency"}),
							frappe.format(unallocated, {"fieldtype": "Currency"}),
						)
					)
				unallocated_base = flt(unallocated - vat_on_unallocated, self.precision)
			else:
				unallocated_base = unallocated
			gross_in_party_currency += unallocated_base

		gross_in_party_currency = flt(gross_in_party_currency, self.precision)
		exchange_rate = self.get_conversion_rate() or 1

		category["taxable_amount"] = flt(gross_in_party_currency * exchange_rate, self.precision)
		# Shown read-only on the form so the figure can be checked without
		# reverse-engineering it from the tax row.
		self.doc.custom_wht_base_amount = flt(
			gross_in_party_currency, self.doc.precision("paid_amount")
		)


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


def _vat_component(doc, gross_in_party_currency: float) -> float:
	"""VAT contained in `gross_in_party_currency`, in the party currency.

	Only ever called for the unallocated leg - see the module docstring.
	An explicit VAT Amount wins over the rate: a figure typed off a real
	document is more reliable than a rate that may have changed.
	"""
	if flt(doc.get("custom_vat_amount")):
		return flt(doc.custom_vat_amount)

	rate = flt(doc.get("custom_vat_rate"))
	if not rate:
		return 0.0

	# The amount is VAT-inclusive, so the VAT is backed out of it rather than
	# added on top: 12,000 at 20% contains 2,000 of VAT, not 2,400.
	return flt(gross_in_party_currency) * rate / (100.0 + rate)


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

		# Always re-run, regardless of the VAT checkbox - most of what this
		# computes (reading VAT off a real invoice/order) does not depend on
		# it at all. Only the unallocated leg, inside this call, ever looks at
		# the checkbox.
		PrymagePaymentTaxWithholding(self).on_validate()


def validate_vat_exclusion(doc, method=None):
	"""Guard rails for the VAT fields, independent of the class override.

	Runs as a ``validate`` doc event so it still applies if another app takes
	over the Payment Entry class. These fields only ever affect the
	unallocated leg of the payment - see the module docstring - so a tick with
	nothing unallocated to apply it to is flagged rather than silently ignored.
	"""
	if not doc.get("custom_payment_includes_vat"):
		return

	if not doc.get("apply_tds"):
		frappe.throw(
			_(
				"'Payment Includes VAT' only affects the withholding tax calculation. "
				"Tick 'Apply Tax Withholding Amount' as well, or untick this."
			)
		)

	if not flt(doc.get("unallocated_amount")):
		frappe.msgprint(
			_(
				"'Payment Includes VAT' only affects the unallocated portion of this "
				"payment, and this payment has none - it is fully allocated against "
				"invoices/orders, each of which already supplies its own VAT "
				"breakdown automatically. This tick currently has no effect."
			),
			indicator="orange",
			alert=True,
		)
		return

	if flt(doc.get("custom_vat_amount")) and flt(doc.get("custom_vat_rate")):
		frappe.msgprint(
			_(
				"Both a VAT Rate and a VAT Amount are set. The VAT Amount ({0}) is used; "
				"the rate is ignored."
			).format(frappe.format(doc.custom_vat_amount, {"fieldtype": "Currency"})),
			indicator="orange",
			alert=True,
		)

	if not flt(doc.get("custom_vat_amount")) and not flt(doc.get("custom_vat_rate")):
		frappe.throw(
			_("Enter either a VAT Rate or a VAT Amount, otherwise there is no VAT to exclude.")
		)

	if flt(doc.get("custom_vat_rate")) < 0 or flt(doc.get("custom_vat_rate")) >= 100:
		frappe.throw(_("VAT Rate must be between 0 and 100."))

	if flt(doc.get("custom_vat_amount")) < 0:
		frappe.throw(_("VAT Amount cannot be negative."))
