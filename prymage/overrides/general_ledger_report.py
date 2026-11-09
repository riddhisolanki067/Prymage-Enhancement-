"""Monkey-patches ERPNext's stock General Ledger report (requirement C,
"Presentation in the report") so the same withholding-tax split built for
the Prymage Ledger Statement also shows on the page the client actually
uses day to day to check a supplier's ledger.

There is no supported extension point for this: General Ledger is a plain
module function with no hook in it (verified directly in
erpnext/accounts/report/general_ledger/general_ledger.py - no doc_events,
no overridable method, nothing in hooks.py it reads). Patching the function
itself is patching another app's internals, not a sanctioned Frappe
mechanism - a future ERPNext version could change ``execute()``'s signature
or behaviour and silently break this with no warning at patch-time. Chosen
explicitly, by the client's own choice, over the safer alternative (a
separate report of our own that would need opening deliberately instead of
the page already in muscle memory).

The split itself never touches the real GL/invoice-reconciliation data -
see ``split_withholding_lines`` in the Prymage Ledger Statement module for
why that line was drawn. This file only decides where else, as data already
leaving ``execute()``, that same display-only split gets applied.
"""

import frappe

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
			from prymage.prymage.doctype.prymage_ledger_statement.prymage_ledger_statement import (
				split_withholding_lines,
			)

			precision = frappe.get_precision("GL Entry", "debit") or 2
			res = split_withholding_lines(res, precision)
		except Exception:
			# Fail open: a bug in our presentation-only split must never take
			# down the report she relies on for real accounting work.
			frappe.log_error(title="Prymage: General Ledger withholding split failed")

		return columns, res

	setattr(patched_execute, _PATCHED_FLAG, True)
	gl_report.execute = patched_execute
