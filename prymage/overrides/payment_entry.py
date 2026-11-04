"""Withholding tax on the amount excluding VAT (requirement C).

What ERPNext v16 already does, verified in source
(``erpnext/accounts/doctype/tax_withholding_entry/tax_withholding_entry.py``):

* **On a Purchase Invoice** the taxable amount is ``item.base_net_amount``
  unless the Tax Withholding Category's ``tax_deduction_basis`` is
  ``Gross Total``. The field ships defaulted to ``Net Total``, so withholding
  taken at the invoice already excludes VAT. Nothing to build - that is why
  requirement C line 10 is a configuration and process correction, and why
  ``purchase_invoice.validate_withholding_basis`` only warns when a category is
  set to ``Gross Total``.

* **On a Payment Entry** ``PaymentTaxWithholding._update_taxable_amounts`` uses
  ``unallocated_amount`` plus anything allocated to an advance payment document
  (Sales Order / Purchase Order). It does not look at
  ``tax_deduction_basis`` and it has no way to know what part of the payment is
  VAT - there is no invoice to take the VAT out of. That is the gap this module
  closes (requirement C line 11).

Amounts allocated against an *invoice* are deliberately excluded from the
withholding base by ERPNext, because the invoice has already withheld. This
module does not change that.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import (
	PaymentTaxWithholding,
)


class PrymageAdvancePaymentTaxWithholding(PaymentTaxWithholding):
	"""Same engine, with VAT taken out of the taxable amount."""

	def _update_taxable_amounts(self):
		super()._update_taxable_amounts()

		category = next(iter(self.category_details.values()), None)
		if not category:
			return

		gross = flt(category.get("taxable_amount"))
		if not gross:
			self.doc.custom_wht_base_amount = 0
			return

		exchange_rate = self.get_conversion_rate() or 1
		vat_in_base_currency = flt(
			_vat_component(self.doc, gross / exchange_rate) * exchange_rate, self.precision
		)

		net = flt(gross - vat_in_base_currency, self.precision)
		if net < 0:
			frappe.throw(
				_(
					"The VAT entered ({0}) is larger than the amount the withholding tax "
					"would be calculated on ({1})."
				).format(
					frappe.format(vat_in_base_currency / exchange_rate, {"fieldtype": "Currency"}),
					frappe.format(gross / exchange_rate, {"fieldtype": "Currency"}),
				)
			)

		category["taxable_amount"] = net
		# Shown read-only on the form so the figure can be checked without
		# reverse-engineering it from the tax row.
		self.doc.custom_wht_base_amount = flt(net / exchange_rate, self.doc.precision("paid_amount"))


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

		PrymageAdvancePaymentTaxWithholding(self).on_validate()


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
