"""Withholding tax on the amount excluding VAT (requirement C).

The requirement document's own wording and worked example ("Invoice Total:
GHS 12,000.00 ... Net Payment to Supplier: GHS 11,250.00") describe a Payment
Entry that settles an invoice, not specifically a payment made in advance of
one. ``PaymentTaxWithholding._update_taxable_amounts`` - verified in source,
``erpnext/accounts/doctype/tax_withholding_entry/tax_withholding_entry.py`` -
only ever counts ``unallocated_amount`` plus amounts allocated to a Sales/
Purchase *Order*; an amount allocated against an *Invoice* is always excluded,
because ERPNext assumes the invoice already withheld. This module applies on
any Payment Entry with VAT - settling an invoice or not - with one guard:

**an allocation is only ever counted once.** If the invoice being settled
already had ``apply_tds`` ticked, it has already withheld (requirement C,
line 10, which needs no code - ERPNext gets that case right by itself). This
module excludes exactly that allocation from the Payment Entry's base, so the
same transaction is never withheld twice. An allocation against an invoice
that was *not* itself withholding (or against a Sales/Purchase Order, which
never withholds) is counted normally.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import (
	PaymentTaxWithholding,
)


class PrymagePaymentTaxWithholding(PaymentTaxWithholding):
	"""Same engine, with VAT taken out of the taxable amount.

	Does not call ``super()._update_taxable_amounts()`` - the base
	implementation's own allocation filter is exactly what needs widening, so
	there is nothing to inherit from it here.
	"""

	def _update_taxable_amounts(self):
		category = next(iter(self.category_details.values()), None)
		if not category:
			return

		gross_in_party_currency = flt(self.doc.unallocated_amount)
		for row in self.doc.references:
			if _reference_already_withheld(row.reference_doctype, row.reference_name):
				continue
			gross_in_party_currency += flt(row.allocated_amount)

		exchange_rate = self.get_conversion_rate() or 1
		gross = flt(gross_in_party_currency * exchange_rate, self.precision)

		if not gross:
			category["taxable_amount"] = 0
			self.doc.custom_wht_base_amount = 0
			return

		vat_in_party_currency = _vat_component(self.doc, gross_in_party_currency)
		vat = flt(vat_in_party_currency * exchange_rate, self.precision)

		net = flt(gross - vat, self.precision)
		if net < 0:
			frappe.throw(
				_(
					"The VAT entered ({0}) is larger than the amount the withholding tax "
					"would be calculated on ({1})."
				).format(
					frappe.format(vat_in_party_currency, {"fieldtype": "Currency"}),
					frappe.format(gross_in_party_currency, {"fieldtype": "Currency"}),
				)
			)

		category["taxable_amount"] = net
		# Shown read-only on the form so the figure can be checked without
		# reverse-engineering it from the tax row.
		self.doc.custom_wht_base_amount = flt(net / exchange_rate, self.doc.precision("paid_amount"))


def _reference_already_withheld(reference_doctype: str, reference_name: str) -> bool:
	"""Did this reference's own document already withhold tax on itself?

	Only an Invoice can - a Sales/Purchase Order is never accounting-posted and
	never has an ``apply_tds`` of its own, so it is never excluded here.
	"""
	if not frappe.get_meta(reference_doctype).has_field("apply_tds"):
		return False

	return bool(frappe.db.get_value(reference_doctype, reference_name, "apply_tds"))


def _vat_component(doc, gross_in_party_currency: float) -> float:
	"""VAT contained in `gross_in_party_currency`, in the party currency.

	An explicit VAT Amount wins over the rate: the user typing the figure off
	the invoice is more reliable than a rate that may have changed.
	"""
	if flt(doc.get("custom_vat_amount")):
		return flt(doc.custom_vat_amount)

	rate = flt(doc.get("custom_vat_rate"))
	if not rate:
		return 0.0

	# The paid amount is VAT-inclusive, so the VAT is backed out of it rather
	# than added on top: 12,000 at 20% contains 2,000 of VAT, not 2,400.
	return flt(gross_in_party_currency) * rate / (100.0 + rate)


class PrymagePaymentEntryExtension:
	"""Re-runs the withholding calculation with the VAT excluded.

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

		if not self.get("custom_payment_includes_vat"):
			self.custom_wht_base_amount = 0
			return

		if not self.apply_tds or not self.tax_withholding_category:
			self.custom_wht_base_amount = 0
			return

		PrymagePaymentTaxWithholding(self).on_validate()


def validate_vat_exclusion(doc, method=None):
	"""Guard rails for the VAT fields, independent of the class override.

	Runs as a ``validate`` doc event so it still applies if another app takes
	over the Payment Entry class.
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
