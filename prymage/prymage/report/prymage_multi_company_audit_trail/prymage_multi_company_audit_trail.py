# Copyright (c) 2026, Riddhi and contributors
# For license information, please see license.txt
"""Audit trail of changes to Prymage Multi Company Settings (requirement A6).

Frappe's generic version-tracking (``track_changes: 1`` on the doctype) is
the *source* of this data - that stays on, and nothing here replaces it. What
this report adds is a presentable, filterable table on top of it: who changed
what, for which company, from what to what - rather than making a System
Manager open the settings form and read raw diffs out of the Activity panel.

Every row comes from a real ``Version`` record; nothing here is inferred or
reconstructed.
"""

import json

import frappe
from frappe import _
from frappe.utils import cstr, get_datetime

#: Human labels for every field this report can ever show. Deliberately a
#: fixed map rather than ``frappe.unscrub`` - a client reading an audit log
#: should see exactly the label they see on the form, not a guessed one.
FIELD_LABELS = {
	"enable_company_data_isolation": "Enable Company Data Isolation",
	"require_company_selection_on_login": "Require Company Selection on Login",
	"skip_selection_for_single_company": "Skip Screen for Single-Company Users",
	"allow_multiple_company_selection": "Allow Selecting Multiple Companies",
	"share_customers": "Share Customers",
	"share_suppliers": "Share Suppliers",
	"share_items": "Share Items",
	"share_price_lists": "Share Price Lists",
	"share_contacts_and_addresses": "Share Contacts & Addresses",
}

#: Every field this doctype carries today is a Check field, so every value
#: shown is Yes/No rather than a raw 0/1.
BOOLEAN_FIELDS = set(FIELD_LABELS)

SHARING_FIELDS = (
	"share_customers",
	"share_suppliers",
	"share_items",
	"share_price_lists",
	"share_contacts_and_addresses",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Changed On"), "fieldname": "changed_on", "fieldtype": "Datetime", "width": 160},
		{"label": _("Changed By"), "fieldname": "changed_by", "fieldtype": "Link", "options": "User", "width": 180},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 160},
		{"label": _("Change"), "fieldname": "change_type", "fieldtype": "Data", "width": 140},
		{"label": _("Field Changed"), "fieldname": "field_label", "fieldtype": "Data", "width": 230},
		{"label": _("Old Value"), "fieldname": "old_value", "fieldtype": "Data", "width": 140},
		{"label": _("New Value"), "fieldname": "new_value", "fieldtype": "Data", "width": 140},
	]


def get_data(filters):
	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Prymage Multi Company Settings"},
		fields=["name", "data", "owner", "creation"],
		order_by="creation desc",
	)

	row_company = _build_row_company_map(versions)

	rows = []
	for version in versions:
		if not version.data:
			continue
		try:
			diff = json.loads(version.data)
		except ValueError:
			continue

		changed_on = version.creation
		changed_by = version.owner

		if filters.get("from_date") and get_datetime(changed_on) < get_datetime(filters.from_date):
			continue
		if filters.get("to_date") and get_datetime(changed_on) > get_datetime(filters.to_date):
			continue
		if filters.get("changed_by") and changed_by != filters.changed_by:
			continue

		# Top-level settings - not tied to any one company.
		for fieldname, old, new in diff.get("changed", []):
			rows.append(_row(changed_on, changed_by, None, _("Setting changed"), fieldname, old, new))

		# A sharing box ticked/unticked for one company's row.
		for _fieldname, _idx, row_name, child_changes in diff.get("row_changed", []):
			company = row_company.get(row_name)
			for child_field, old, new in child_changes:
				rows.append(_row(changed_on, changed_by, company, _("Sharing changed"), child_field, old, new))

		# A new company's sharing row was created (e.g. a Company was added).
		for _fieldname, row_dict in diff.get("added", []):
			rows.append(
				_row(
					changed_on,
					changed_by,
					row_dict.get("company"),
					_("Company added"),
					None,
					None,
					_describe_row(row_dict),
				)
			)

		# A company's sharing row was removed (e.g. a Company was deleted).
		for _fieldname, row_dict in diff.get("removed", []):
			rows.append(
				_row(
					changed_on,
					changed_by,
					row_dict.get("company"),
					_("Company removed"),
					None,
					_describe_row(row_dict),
					None,
				)
			)

	if filters.get("company"):
		rows = [r for r in rows if r["company"] == filters.company]

	return rows


def _build_row_company_map(versions) -> dict:
	"""Map a Company Data Sharing row's own name to the company it belongs to.

	``row_changed`` only carries the row's internal name, not which company it
	is - so that has to be resolved separately. Built two ways: from every
	"added"/"removed" diff ever recorded (which carry the full row, company
	included), and topped up from the table as it stands today, so a row that
	was seeded at install (never itself "added" in a tracked version) still
	resolves.
	"""
	mapping = {}

	for version in versions:
		if not version.data:
			continue
		try:
			diff = json.loads(version.data)
		except ValueError:
			continue
		for _fieldname, row_dict in diff.get("added", []) + diff.get("removed", []):
			if isinstance(row_dict, dict) and row_dict.get("name") and row_dict.get("company"):
				mapping.setdefault(row_dict["name"], row_dict["company"])

	for row in frappe.get_all(
		"Prymage Company Data Sharing",
		filters={"parent": "Prymage Multi Company Settings"},
		fields=["name", "company"],
	):
		mapping.setdefault(row.name, row.company)

	return mapping


def _row(changed_on, changed_by, company, change_type, fieldname, old, new):
	label = _(FIELD_LABELS.get(fieldname, fieldname)) if fieldname else _("Sharing settings (all fields)")
	return {
		"changed_on": changed_on,
		"changed_by": changed_by,
		"company": company,
		"change_type": change_type,
		"field_label": label,
		"old_value": _format_value(fieldname, old),
		"new_value": _format_value(fieldname, new),
	}


def _format_value(fieldname, value):
	if value in (None, ""):
		return ""
	if fieldname in BOOLEAN_FIELDS:
		return _("Yes") if cstr(value) in ("1", "1.0", "True", "true") else _("No")
	return cstr(value)


def _describe_row(row_dict: dict) -> str:
	shared = [_(FIELD_LABELS[f]) for f in SHARING_FIELDS if row_dict.get(f)]
	return ", ".join(shared) if shared else _("nothing shared")
