"""Company tag on shared master records (requirement A4).

A new record is stamped with the company the user is working in, so the
visibility rules in ``permissions.py`` have something to read. Existing records
are deliberately left untagged - an untagged record counts as shared, which is
why installing this app changes nothing on day one.
"""

import frappe

from prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings import (
	get_settings,
	is_shareable,
)
from prymage.multi_company.session import get_primary_company

OWNER_FIELD = "custom_owner_company"


def set_owner_company(doc, method=None):
	"""Registered under the "*" wildcard doctype key in hooks.py - runs on
	every single insert in the whole system, not just the registry's own
	doctypes, so ``is_shareable()`` (one cached in-memory set lookup) has to
	be the very first thing this does, before anything that costs more than
	that for the other 99% of doctypes it is now also called for.
	"""
	if not is_shareable(doc.doctype):
		return

	# A field we rely on could be missing if the install step was skipped.
	if not doc.meta.get_field(OWNER_FIELD):
		return

	if doc.get(OWNER_FIELD):
		# Explicitly set by the user or by an import: never overwrite.
		return

	settings = get_settings()
	if not settings.enabled:
		return

	company = get_primary_company()
	if not company:
		return

	doc.set(OWNER_FIELD, company)


@frappe.whitelist()
def tag_existing_records(company: str, doctype: str | None = None, dry_run: int = 1):
	"""Back-fill the company tag on records that have none.

	Used once, at the point the client decides to switch isolation on. Returns
	the counts it would change when ``dry_run`` is set.
	"""
	frappe.only_for("System Manager")

	if not frappe.db.exists("Company", company):
		frappe.throw(frappe._("Company {0} does not exist.").format(company))

	doctypes = [doctype] if doctype else sorted(get_settings().shareable_doctypes)
	result = {}

	for dt in doctypes:
		if not is_shareable(dt):
			frappe.throw(frappe._("{0} is not a company-tagged master.").format(dt))

		names = frappe.get_all(
			dt,
			filters=[[OWNER_FIELD, "in", [None, ""]]],
			pluck="name",
			ignore_permissions=True,
			limit_page_length=0,
		)
		result[dt] = len(names)

		if not int(dry_run or 0) and names:
			frappe.db.set_value(
				dt,
				{"name": ("in", names)},
				OWNER_FIELD,
				company,
				update_modified=False,
			)

	return {"dry_run": bool(int(dry_run or 0)), "counts": result}
