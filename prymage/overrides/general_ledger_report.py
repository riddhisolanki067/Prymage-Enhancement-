"""Monkey-patches ERPNext's stock General Ledger report (requirement C,
"Presentation in the report") so a Payment Entry that withholds tax shows
the withheld amount as its own line, separate from the net payment, when
the report is filtered by a Supplier/Customer or an Account - the page the
client actually uses day to day, with no custom doctype of ours anywhere
in the picture (explicitly rejected in favour of this).

There is no supported extension point for this: General Ledger is a plain
module function with no hook in it (verified directly in
erpnext/accounts/report/general_ledger/general_ledger.py - no doc_events,
no overridable method, nothing in hooks.py it reads). Patching the function
itself is patching another app's internals, not a sanctioned Frappe
mechanism - a future ERPNext version could change ``execute()``'s signature
or behaviour and silently break this with no warning at patch-time. Chosen
explicitly, by the client, over the safer alternative (a separate report
of our own that would need opening deliberately instead of the page
already in muscle memory).

Requirement D's three print formats (Customer / Supplier / plain Account)
are built directly against this same report - see
``prymage/prymage/print_format/`` - rather than against any document of
ours: Frappe's report Print dialog can render a named Print Format against
the report's own already-computed ``data``/``columns``, the exact rows
shown on screen, split included. No separate doctype, no recomputed
balances - what prints is what this patch already produced.
"""

import frappe
from frappe import _
from frappe.utils import flt

_PATCHED_FLAG = "_prymage_patched"


def patch_general_ledger_report():
	"""Called once, from prymage/__init__.py, at first import of this app."""
	try:
		import erpnext.accounts.report.general_ledger.general_ledger as gl_report
	except ImportError:
		# erpnext not importable yet in whatever context first imported
		# prymage (e.g. an unusual bench command order) - leave the stock
		# report untouched rather than fail app boot over a cosmetic feature.
		return

	if getattr(gl_report.execute, _PATCHED_FLAG, False):
		return

	original_execute = gl_report.execute

	def patched_execute(filters=None):
		columns, res = original_execute(filters)

		try:
			precision = frappe.get_precision("GL Entry", "debit") or 2
			res = split_withholding_lines(res, precision)
		except Exception:
			# Fail open: a bug in our presentation-only split must never take
			# down the report she relies on for real accounting work.
			frappe.log_error(title="Prymage: General Ledger withholding split failed")

		return columns, res

	setattr(patched_execute, _PATCHED_FLAG, True)
	gl_report.execute = patched_execute


def split_withholding_lines(entries: list, precision: int) -> list:
	"""Split a Payment Entry's one combined party-ledger line into two
	*display* rows - net payment and withholding deducted - that still sum
	to exactly the original debit/credit. Every other row, and every balance
	already computed by the report, is untouched.

	ERPNext's own GL posting for a Payment Entry that withholds tax is
	already correct money-wise - the withheld amount genuinely never leaves
	the bank account, and the books balance. It just attaches that
	adjustment to the *bank* account's ledger, not the party's, so a party-
	filtered General Ledger query only ever shows the one combined line.
	Reworking that at the GL-posting level would mean also reworking how
	invoice outstanding-amount reconciliation reads these entries - real
	risk of an invoice permanently showing as outstanding when it has
	actually been fully settled. The requirement document itself scopes
	this as presentation, not posting, so it stops there.

	General Ledger's own Opening/Total/Closing summary rows carry no
	``voucher_type``/``voucher_no`` key at all (not even ``None``) - every
	access below goes through ``.get()`` for exactly that reason, not out of
	excess caution.
	"""
	pe_names = {e.get("voucher_no") for e in entries if e.get("voucher_type") == "Payment Entry"}
	pe_names.discard(None)
	if not pe_names:
		return entries

	withheld_by_voucher: dict[str, float] = {}
	for row in frappe.get_all(
		"Advance Taxes and Charges",
		filters={
			"parenttype": "Payment Entry",
			"parent": ("in", list(pe_names)),
			"is_tax_withholding_account": 1,
		},
		fields=["parent", "tax_amount"],
	):
		withheld_by_voucher[row.parent] = withheld_by_voucher.get(row.parent, 0) + flt(row.tax_amount)
	withheld_by_voucher = {k: v for k, v in withheld_by_voucher.items() if v}
	if not withheld_by_voucher:
		return entries

	occurrences: dict[str, int] = {}
	for e in entries:
		if e.get("voucher_type") == "Payment Entry":
			occurrences[e.get("voucher_no")] = occurrences.get(e.get("voucher_no"), 0) + 1

	result = []
	for e in entries:
		withheld = (
			withheld_by_voucher.get(e.get("voucher_no"))
			if e.get("voucher_type") == "Payment Entry"
			else None
		)
		# Only split a voucher with exactly one line in this party's ledger -
		# a payment withholding against several references at once would need
		# to attribute the single combined tax amount across several rows,
		# which this does not attempt; such a row is left exactly as posted.
		if not withheld or occurrences.get(e.get("voucher_no")) != 1:
			result.append(e)
			continue

		original_debit, original_credit = flt(e.get("debit")), flt(e.get("credit"))
		side, original = ("debit", original_debit) if original_debit else ("credit", original_credit)
		if withheld >= original:
			result.append(e)
			continue

		previous_balance = flt(flt(e.get("balance")) - original_debit + original_credit, precision)
		net_amount = flt(original - withheld, precision)

		net_row = dict(e)
		net_row["debit"] = net_amount if side == "debit" else 0
		net_row["credit"] = net_amount if side == "credit" else 0
		net_row["balance"] = flt(previous_balance + net_row["debit"] - net_row["credit"], precision)
		result.append(net_row)

		wht_row = dict(e)
		wht_row["debit"] = flt(withheld, precision) if side == "debit" else 0
		wht_row["credit"] = flt(withheld, precision) if side == "credit" else 0
		# No narration column on this report - Against Account is the only
		# visible slot available to label the split-out row.
		wht_row["against"] = _("Withholding Tax Deducted")
		wht_row["balance"] = flt(e.get("balance"))
		result.append(wht_row)

	return result
