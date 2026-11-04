# Copyright (c) 2026, Riddhi and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

CACHE_KEY = "prymage_multi_company_settings"

SHARE_FIELD_BY_DOCTYPE = {
	"Customer": "share_customers",
	"Supplier": "share_suppliers",
	"Item": "share_items",
	"Price List": "share_price_lists",
	"Contact": "share_contacts_and_addresses",
	"Address": "share_contacts_and_addresses",
}


class PrymageMultiCompanySettings(Document):
	def validate(self):
		self.validate_unique_companies()

	def validate_unique_companies(self):
		seen = set()
		for row in self.company_data_sharing:
			if row.company in seen:
				frappe.throw(
					_("Company {0} is listed more than once in Company Data Sharing.").format(
						frappe.bold(row.company)
					)
				)
			seen.add(row.company)

	def on_update(self):
		clear_settings_cache()

	def sync_companies(self) -> bool:
		"""Reconcile the sharing table with the Company list.

		New companies default to *sharing* everything, so adding a company can
		never silently hide master data that used to be visible. Rows for
		companies that no longer exist are dropped, otherwise a deleted company
		keeps contributing to the shared pool.
		"""
		companies = frappe.get_all("Company", pluck="name", order_by="name")
		existing = {row.company for row in self.company_data_sharing}
		changed = False

		stale = [row for row in self.company_data_sharing if row.company not in companies]
		if stale:
			self.set(
				"company_data_sharing",
				[row for row in self.company_data_sharing if row.company in companies],
			)
			changed = True

		for company in companies:
			if company not in existing:
				self.append(
					"company_data_sharing",
					{
						"company": company,
						"share_customers": 1,
						"share_suppliers": 1,
						"share_items": 1,
						"share_price_lists": 1,
						"share_contacts_and_addresses": 1,
					},
				)
				changed = True

		return changed


def clear_settings_cache():
	frappe.cache.delete_value(CACHE_KEY)


def get_settings() -> frappe._dict:
	"""Cached, read-only view of the settings.

	Read on nearly every list query, so it must not hit the database each time.
	"""

	def _load():
		doc = frappe.get_cached_doc("Prymage Multi Company Settings")
		return {
			"enabled": bool(doc.enable_company_data_isolation),
			"treat_untagged_as_shared": bool(doc.treat_untagged_as_shared),
			"require_selection": bool(doc.require_company_selection_on_login),
			"skip_single": bool(doc.skip_selection_for_single_company),
			"allow_multiple": bool(doc.allow_multiple_company_selection),
			"sharing": {
				row.company: {
					field: bool(row.get(field)) for field in set(SHARE_FIELD_BY_DOCTYPE.values())
				}
				for row in doc.company_data_sharing
			},
		}

	return frappe._dict(frappe.cache.get_value(CACHE_KEY, _load) or _load())


def shares(settings: frappe._dict, company: str, doctype: str) -> bool:
	"""Does `company` participate in the shared pool for `doctype`?"""
	field = SHARE_FIELD_BY_DOCTYPE.get(doctype)
	if not field:
		return False
	return bool(settings.sharing.get(company, {}).get(field))


@frappe.whitelist()
def sync_companies():
	frappe.only_for("System Manager")
	doc = frappe.get_single("Prymage Multi Company Settings")
	if doc.sync_companies():
		doc.save()
		return {"added": True}
	return {"added": False}


def on_company_trash(doc, method=None):
	"""Drop a deleted company's sharing row (and its company tags).

	Without this the row lingers and a deleted company keeps granting access to
	the shared pool. Master records tagged with it are released back to
	untagged, which is the shared state, so nothing becomes unreachable.
	"""
	settings = frappe.get_single("Prymage Multi Company Settings")
	rows = [row for row in settings.company_data_sharing if row.company != doc.name]
	if len(rows) != len(settings.company_data_sharing):
		settings.set("company_data_sharing", rows)
		settings.save(ignore_permissions=True)

	for doctype in SHARE_FIELD_BY_DOCTYPE:
		if frappe.get_meta(doctype).get_field("custom_owner_company"):
			frappe.db.set_value(
				doctype,
				{"custom_owner_company": doc.name},
				"custom_owner_company",
				None,
				update_modified=False,
			)

	from prymage.multi_company.session import clear_request_cache

	clear_settings_cache()
	clear_request_cache()
