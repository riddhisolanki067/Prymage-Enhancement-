"""Custom fields added by the Prymage app.

Every field is prefixed ``custom_`` (Frappe's reserved prefix for fields added
to standard doctypes) so an ERPNext upgrade can never collide with them.

Defined here rather than shipped as fixtures: fixtures are re-imported on every
``bench migrate`` and silently resurrect fields deleted in a patch, which makes
the field set impossible to retire cleanly.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# A4 - the master doctypes that get a company tag. ERPNext keeps these as one
# global list; warehouses, cost centers and projects are already
# company-specific, so they are deliberately absent.
SHARED_MASTERS = {
	"Customer": "language",
	"Supplier": "release_date",
	"Item": "image",
	"Price List": "currency",
	"Contact": "company_name",
	"Address": "address_type",
}

OWNER_COMPANY_FIELD = "custom_owner_company"

# Fields once defined below, now retired. ``create_custom_fields`` only
# creates/updates what is in ``get_custom_fields()`` - it never deletes a
# field that used to be there, so a retired field has to be dropped
# explicitly or it lingers on the form forever.
RETIRED_FIELDS = {
	"Payment Entry": ["custom_vat_rate", "custom_vat_amount"],
}


def _owner_company_field(insert_after: str) -> list[dict]:
	return [
		{
			"fieldname": "custom_prymage_company_section",
			"label": "Company Ownership",
			"fieldtype": "Section Break",
			"insert_after": insert_after,
			"collapsible": 1,
		},
		{
			"fieldname": OWNER_COMPANY_FIELD,
			"label": "Owned by Company",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "custom_prymage_company_section",
			"in_standard_filter": 1,
			"ignore_user_permissions": 1,
			"description": (
				"The company this record belongs to. Blank means the record is "
				"shared with every company. Set automatically from the company "
				"you are working in when Multi Company data isolation is on."
			),
		},
	]


def get_custom_fields() -> dict:
	fields: dict[str, list[dict]] = {}

	for doctype, anchor in SHARED_MASTERS.items():
		fields[doctype] = _owner_company_field(anchor)

	# ------------------------------------------------------------------
	# A2 - company access per user. Mirrors the User Permission records on
	# Company; see prymage/multi_company/user_companies.py.
	# ------------------------------------------------------------------
	fields["User"] = [
		{
			"fieldname": "custom_prymage_company_access_tab",
			"label": "Company Access",
			"fieldtype": "Tab Break",
			"insert_after": "api_secret",
			"depends_on": "eval:doc.user_type=='System User'",
		},
		{
			"fieldname": "custom_allowed_companies",
			"label": "Allowed Companies",
			"fieldtype": "Table",
			"options": "Prymage User Company",
			"insert_after": "custom_prymage_company_access_tab",
			"description": (
				"Companies this user may work in. Saving the user writes these "
				"rows to User Permission records on Company, which is what "
				"actually restricts the data the user can see."
			),
		},
	]

	# ------------------------------------------------------------------
	# C - withholding tax on the amount excluding VAT
	# ------------------------------------------------------------------
	fields["Payment Entry"] = [
		{
			"fieldname": "custom_prymage_wht_section",
			"label": "Withholding Tax on Net of VAT",
			"fieldtype": "Section Break",
			"insert_after": "tax_withholding_category",
			"depends_on": "eval:doc.apply_tds",
			"collapsible": 0,
		},
		{
			"fieldname": "custom_wht_base_amount",
			"label": "Withholding Tax Base",
			"fieldtype": "Currency",
			"options": "paid_from_account_currency",
			"insert_after": "custom_prymage_wht_section",
			"read_only": 1,
			# Explicitly cleared - see the matching comment on
			# custom_payment_includes_vat below for why this can't be omitted.
			# An earlier design hid this field unless the checkbox was ticked;
			# it is meant to always show now.
			"depends_on": "",
			"description": (
				"The amount withholding tax is actually calculated on. With 'Include "
				"VAT' unticked (the default), VAT is excluded automatically - read "
				"straight off the net total of whichever invoice/order this payment "
				"settles, nothing to enter here. Tick 'Include VAT' to fall back to "
				"standard ERPNext withholding instead, with VAT included."
			),
		},
		{
			"fieldname": "custom_column_break_prymage_wht",
			"fieldtype": "Column Break",
			"insert_after": "custom_wht_base_amount",
		},
		{
			"fieldname": "custom_payment_includes_vat",
			"label": "Include VAT",
			"fieldtype": "Check",
			"insert_after": "custom_column_break_prymage_wht",
			"default": "0",
			# Explicitly cleared, not just omitted: an earlier design scoped this
			# checkbox to the unallocated leg only via this same depends_on.
			# create_custom_fields(update=True) only writes keys present in the
			# dict - a key left out is NOT reset, so an old value lingers in the
			# database until something overwrites it with an explicit "".
			"depends_on": "",
			"description": (
				"Leave unticked to withhold on the VAT-excluded amount, read "
				"automatically off the invoice/order this payment settles. Tick "
				"only to use standard ERPNext withholding instead, which includes "
				"VAT in the base."
			),
		},
	]

	return fields


def setup_custom_fields():
	"""Idempotent: safe to run on every install and migrate."""
	_validate_anchors()
	create_custom_fields(get_custom_fields(), ignore_validate=False, update=True)
	_retire_fields()


def _retire_fields():
	for doctype, fieldnames in RETIRED_FIELDS.items():
		for fieldname in fieldnames:
			docname = f"{doctype}-{fieldname}"
			if frappe.db.exists("Custom Field", docname):
				frappe.delete_doc("Custom Field", docname, ignore_permissions=True)


def _validate_anchors():
	"""Fail loudly if an ERPNext upgrade removed a field we insert after.

	Without this the custom field silently lands at the bottom of the form and
	nobody notices until a user cannot find it.
	"""
	problems = []
	for doctype, fields in get_custom_fields().items():
		meta = frappe.get_meta(doctype)
		own = {f["fieldname"] for f in fields}
		for df in fields:
			anchor = df.get("insert_after")
			if not anchor or anchor in own:
				continue
			if not meta.get_field(anchor) and anchor not in (
				meta.get_valid_columns() if hasattr(meta, "get_valid_columns") else []
			):
				problems.append(f"{doctype}.{df['fieldname']} -> insert_after '{anchor}' not found")

	if problems:
		frappe.log_error(
			title="Prymage: custom field anchors missing",
			message="\n".join(problems),
		)
		print("WARNING Prymage custom field anchors missing:\n  " + "\n  ".join(problems))
