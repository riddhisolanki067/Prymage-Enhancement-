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
``prymage/fixtures/print_format.json`` - rather than against any document of
ours: Frappe's report Print dialog can render a named Print Format against
the report's own already-computed ``data``/``columns``, the exact rows
shown on screen, split included. No separate doctype, no recomputed
balances - what prints is what this patch already produced.

To match the client's own reference layout for those three formats exactly,
each row also needs a cheque number, mode of payment and remarks - none of
which the stock report selects by default (``remarks`` only when the
"Show Remarks" filter is ticked; the other two never). ``_enrich_for_print``
below adds them unconditionally, the same way requirement D's own,
now-deleted doctype used to (``VOUCHER_FIELD_MAP`` is the same idea, kept
here since this is the only place left that needs it).
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

		# Enrichment first, split second: the split's own synthetic
		# "Withholding Tax Deducted" row sets its own narration deliberately
		# (see split_withholding_lines) - if enrichment ran after, its
		# blanket "every row gets a narration" pass would overwrite that
		# right back to the voucher's ordinary payment type/voucher type.
		# Running first just means both split-out rows (net and withheld)
		# inherit the same cheque_no/mode_of_payment/remarks from the one
		# real voucher they came from, which is correct either way.
		try:
			res = _enrich_for_print(res)
		except Exception:
			frappe.log_error(title="Prymage: General Ledger print enrichment failed")

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
		# Against Account is the label on the stock report's own columns;
		# narration is the label on the Customer/Supplier print formats,
		# which have no Against Account column at all - both get set so the
		# split-out row reads correctly wherever it ends up being shown.
		wht_row["against"] = _("Withholding Tax Deducted")
		if "narration" in wht_row:
			wht_row["narration"] = _("Withholding Tax Deducted")
		wht_row["balance"] = flt(e.get("balance"))
		result.append(wht_row)

	return result


#: Where the cheque number, mode of payment and payment type live on each
#: voucher type the print formats need them for. Resolved against the
#: doctype meta at run time, so a field that does not exist on a given
#: ERPNext version degrades to blank instead of raising.
VOUCHER_FIELD_MAP = {
	"Payment Entry": {
		"cheque_no": "reference_no",
		"mode_of_payment": "mode_of_payment",
		"payment_type": "payment_type",
	},
	"Journal Entry": {
		"cheque_no": "cheque_no",
		"mode_of_payment": "mode_of_payment",
	},
}


def _enrich_for_print(entries: list) -> list:
	"""Adds ``cheque_no``, ``mode_of_payment``, ``narration`` and ``remarks``
	to every row - none of which the stock report selects by default
	(``remarks`` only with "Show Remarks" ticked; the other three never) -
	needed to match the client's own reference ledger-statement layout. Pure
	addition: nothing already on a row is touched, the normal on-screen
	grid ignores keys its own columns do not reference, so this has no
	effect there.

	``narration`` here means "what kind of transaction was this", not an
	actual Narration/remarks field: the client's own reference print format
	shows the bare payment type (``Pay`` / ``Receive``) for a Payment Entry,
	and the plain voucher type (e.g. ``Purchase Invoice``) for anything
	else - never a reworded label.
	"""
	names_by_type: dict[str, set] = {}
	gl_entry_names = set()
	for e in entries:
		voucher_type, voucher_no = e.get("voucher_type"), e.get("voucher_no")
		if voucher_type and voucher_no:
			names_by_type.setdefault(voucher_type, set()).add(voucher_no)
		if e.get("gl_entry"):
			gl_entry_names.add(e["gl_entry"])

	voucher_details: dict[tuple, dict] = {}
	for voucher_type, names in names_by_type.items():
		mapping = VOUCHER_FIELD_MAP.get(voucher_type)
		if not mapping or not frappe.db.exists("DocType", voucher_type):
			continue
		meta = frappe.get_meta(voucher_type)
		available = {key: field for key, field in mapping.items() if meta.get_field(field)}
		if not available:
			continue
		for row in frappe.get_all(
			voucher_type,
			filters={"name": ("in", list(names))},
			fields=["name", *available.values()],
			ignore_permissions=True,
		):
			voucher_details[(voucher_type, row.name)] = {
				key: row.get(field) for key, field in available.items()
			}

	remarks_by_gl_entry: dict[str, str] = {}
	if gl_entry_names:
		for row in frappe.get_all(
			"GL Entry", filters={"name": ("in", list(gl_entry_names))}, fields=["name", "remarks"]
		):
			if row.remarks:
				remarks_by_gl_entry[row.name] = row.remarks

	for e in entries:
		detail = voucher_details.get((e.get("voucher_type"), e.get("voucher_no")), {})
		e["cheque_no"] = detail.get("cheque_no") or ""
		e["mode_of_payment"] = detail.get("mode_of_payment") or ""
		e["narration"] = detail.get("payment_type") or e.get("voucher_type") or ""
		e["remarks"] = remarks_by_gl_entry.get(e.get("gl_entry")) or ""

	return entries
