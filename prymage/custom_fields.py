"""Custom fields added by the Prymage app.

Every field is prefixed ``custom_`` (Frappe's reserved prefix for fields added
to standard doctypes) so an ERPNext upgrade can never collide with them.

Defined here rather than shipped as fixtures: fixtures are re-imported on every
``bench migrate`` and silently resurrect fields deleted in a patch, which makes
the field set impossible to retire cleanly.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# A4 - which doctypes get a company tag is data, not code: the
# "Shareable DocTypes" table on Prymage Multi Company Settings. Adding a row
# there and saving creates the field below on that doctype immediately - see
# setup_custom_fields() - no hooks.py entry, no migrate, no developer. The
# default 6 (what this app originally shipped with, before that table
# existed) are seeded once by install.py; nothing here reads that seed list
# again afterwards.
#
# Where it lands is computed, not picked: nobody is asked to choose an
# anchor field. _first_tab_anchor() below reads the doctype's own live
# field order to find the last field of whichever tab renders *first* -
# necessary, not just convenient, because a Section Break anchored to a
# field inside a *later* tab renders itself in that later tab, however
# early the field happens to appear in the registry. This must be computed
# against this site's actual form, not assumed from a vanilla ERPNext
# install, which can differ - Customer's field used to be anchored after
# "language", which on this site turned out to render in "More Info", not
# the first tab, the same way Supplier's former anchor "release_date"
# rendered inside "Connections".

OWNER_COMPANY_FIELD = "custom_owner_company"
OWN_FIELDNAMES = {"custom_prymage_company_section", OWNER_COMPANY_FIELD}


def _first_tab_anchor(doctype: str) -> str:
	"""The real fieldname to insert the Company Ownership section after, so
	it lands in whichever tab renders first on this doctype's actual form.

	Ignores this app's own fields if they are already there (a second run on
	a doctype that already has them must not anchor against itself) - the
	anchor is always computed against what the form would look like without
	Prymage's own fields, so it is stable across repeated runs, not just the
	first one.
	"""
	fields = [f for f in frappe.get_meta(doctype).fields if f.fieldname not in OWN_FIELDNAMES]
	if not fields:
		return ""

	tab_break_indexes = [i for i, f in enumerate(fields) if f.fieldtype == "Tab Break"]
	if not tab_break_indexes:
		# No tabs at all - the whole form is one page; append after its
		# last real field, which is functionally the same as "the end of
		# the first (only) tab".
		return fields[-1].fieldname

	first_break = tab_break_indexes[0]
	if first_break > 0:
		# The common case: real fields precede the first Tab Break (an
		# implicit tab Frappe's own UI labels "Details" by default) - anchor
		# on the last of them.
		return fields[first_break - 1].fieldname

	# The doctype's very first field already IS a Tab Break (e.g. Item's own
	# explicit "Details" tab, which still has to count as "the first tab" -
	# anchor on the last field before whatever tab comes *after* it, or the
	# end of the form if that is the only tab there is.
	next_break = tab_break_indexes[1] if len(tab_break_indexes) > 1 else len(fields)
	return fields[next_break - 1].fieldname

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
			# Not a blanket `reqd: 1` - blank is the deliberate safe default
			# (see the description below and permissions.py's own module
			# docstring): an untagged record is unconditionally shared, which
			# is what keeps switching this feature on from hiding anything
			# before a client has tagged their existing data. Mandatory only
			# once Data Isolation is actually on, which is exactly the
			# situation where leaving this blank by accident is a real
			# mistake worth blocking on the form. `isolation_enabled` is
			# already in bootinfo - see prymage/boot.py.
			"mandatory_depends_on": (
				"eval:frappe.boot.prymage_multi_company "
				"&& frappe.boot.prymage_multi_company.isolation_enabled"
			),
			"description": (
				"The company this record belongs to. Blank means the record is "
				"shared with every company. Set automatically from the company "
				"you are working in when Multi Company data isolation is on, "
				"and required on the form whenever isolation is on."
			),
		},
	]


def get_shareable_doctypes() -> list[str]:
	"""Live read of the registry: the list of registered DocType names.

	Empty, not an error, before the first migrate has ever run on a fresh
	site (the table does not exist yet) - ``setup()`` seeds the registry
	before calling ``setup_custom_fields()`` for exactly this reason, but
	nothing here should assume that ordering happened.
	"""
	if not frappe.db.table_exists("Prymage Shareable Doctype"):
		return []
	return frappe.get_all(
		"Prymage Shareable Doctype",
		filters={"parenttype": "Prymage Multi Company Settings"},
		pluck="document_type",
	)


def get_custom_fields() -> dict:
	fields: dict[str, list[dict]] = {}

	for document_type in get_shareable_doctypes():
		if document_type:
			fields[document_type] = _owner_company_field(_first_tab_anchor(document_type))

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
				"settles, nothing to enter here. Tick 'Include VAT' to use the full "
				"allocated/unallocated amount instead, VAT included."
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
				"to withhold on the full amount instead, VAT included."
			),
		},
	]

	return fields


def setup_custom_fields():
	"""Idempotent: safe to run on every install and migrate."""
	_validate_anchors()
	create_custom_fields(get_custom_fields(), ignore_validate=False, update=True)
	_retire_fields()
	_reposition_owner_company_fields()


def _reposition_owner_company_fields():
	"""Force the Company Ownership section back next to its anchor field.

	``create_custom_fields(update=True)`` writes a changed ``insert_after``
	onto an *existing* Custom Field, but that alone does not move it - the
	field's rendered position is driven by ``idx``, which Frappe only derives
	from ``insert_after`` the moment a field is first created. Changing
	``insert_after`` on a field that already exists (exactly the case the
	first time a doctype's *computed* anchor moves - e.g. an ERPNext upgrade
	reorders its form - on a site that already has this field) leaves it
	sitting wherever its *old* anchor put it - confirmed directly against
	this site's own DB after the Customer/Supplier anchor fix, not assumed.

	The sanctioned way to actually move an existing field, without the data
	loss risk of deleting and recreating the Custom Field (which drops the
	column, and therefore every value already stored in it), is the same
	mechanism the Customize Form screen itself uses: reorder the in-memory
	field list and call ``save_customization()`` - see
	``frappe/custom/doctype/customize_form/customize_form.py``, which sets
	``idx`` from list position on save.
	"""
	for doctype in get_shareable_doctypes():
		if not doctype:
			continue
		anchor = _first_tab_anchor(doctype)
		if not anchor:
			# No tab structure and no other fields at all - nothing to
			# reposition against.
			continue

		meta = frappe.get_meta(doctype)
		fieldnames = [f.fieldname for f in meta.fields]
		if anchor not in fieldnames or "custom_prymage_company_section" not in fieldnames:
			continue

		anchor_idx = fieldnames.index(anchor)
		section_idx = fieldnames.index("custom_prymage_company_section")
		if section_idx == anchor_idx + 1:
			# Owner field normally follows the section immediately; already
			# fine if not an older customization put something else there.
			continue

		cf = frappe.get_doc("Customize Form")
		cf.doc_type = doctype
		cf.fetch_to_customize()

		own_names = {"custom_prymage_company_section", OWNER_COMPANY_FIELD}
		own_rows = [r for r in cf.fields if r.fieldname in own_names]
		if len(own_rows) < 2:
			continue

		remaining = [r for r in cf.fields if r.fieldname not in own_names]
		insert_pos = next(i for i, r in enumerate(remaining) if r.fieldname == anchor) + 1
		own_rows.sort(key=lambda r: 0 if r.fieldname == "custom_prymage_company_section" else 1)
		for offset, row in enumerate(own_rows):
			remaining.insert(insert_pos + offset, row)

		cf.fields = remaining
		for i, row in enumerate(cf.fields):
			row.idx = i + 1
		cf.save_customization()


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
