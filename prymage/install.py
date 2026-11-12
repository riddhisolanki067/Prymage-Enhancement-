"""Install and migrate steps. Every one of them is idempotent.

Run by hand after pulling new code:

    bench --site <site> execute prymage.install.after_migrate
"""

import frappe

from prymage.custom_fields import setup_custom_fields
from prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings import (
	DEFAULT_SHAREABLE_DOCTYPES,
	LEGACY_SHARE_FIELD_TO_DOCTYPES,
)
from prymage.prymage.doctype.prymage_subscription_settings.prymage_subscription_settings import (
	ADMIN_ROLE,
)

NAVBAR_ITEM_LABEL = "Switch Company"
NAVBAR_ROUTE = "/app/company-selection"
#: Frappe v16 renders these entries in the sidebar app-switcher dropdown, and
#: that renderer uses the item's `action` as the onclick. An item of type
#: "Route" comes out as a dead link there, so this is registered as an Action.
NAVBAR_ACTION = "frappe.set_route('company-selection')"


def after_install():
	setup()


def after_migrate():
	setup()


def setup():
	create_subscription_admin_role()
	# Order matters: setup_custom_fields() reads the shareable-doctype
	# registry live (prymage/custom_fields.py) - it must be seeded before
	# the first time custom fields are created, or a fresh install creates
	# none of them at all.
	seed_multi_company_settings()
	setup_custom_fields()
	seed_subscription_settings()
	add_switch_company_to_navbar()
	frappe.db.commit()
	print("Prymage: setup complete")


# ---------------------------------------------------------------------------
def create_subscription_admin_role():
	"""Left unassigned on purpose - see guard.settings_has_permission."""
	if frappe.db.exists("Role", ADMIN_ROLE):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": ADMIN_ROLE,
			"desk_access": 1,
			"is_custom": 1,
		}
	).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
def seed_multi_company_settings():
	"""Seed the shareable-doctype registry, migrate the old fixed-column
	sharing table into the new per-doctype matrix once, then reconcile the
	matrix against the current Company list.

	Defaults chosen so installing the app cannot hide a record that was visible
	the day before: the master switch is off, and even with it on every company
	starts out sharing.
	"""
	doc = frappe.get_single("Prymage Multi Company Settings")
	changed = False

	if not doc.shareable_doctypes:
		for document_type, anchor in DEFAULT_SHAREABLE_DOCTYPES.items():
			doc.append(
				"shareable_doctypes",
				{"document_type": document_type, "insert_after_field": anchor},
			)
		changed = True

	if _migrate_legacy_sharing_table(doc):
		changed = True

	# Fills in anything still missing (new companies, or a site with no
	# legacy data at all) regardless of `changed` above.
	if doc.sync_matrix():
		changed = True

	if changed:
		doc.save(ignore_permissions=True)
		print(f"Prymage: company/doctype sharing rows -> {len(doc.company_doctype_sharing)}")


def _migrate_legacy_sharing_table(doc) -> bool:
	"""One-time: fold the old fixed-Check-column sharing table into
	``company_doctype_sharing`` (one row per company x doctype), so a site
	that already had sharing configured keeps it exactly.

	The old table's module files are removed in the same change that adds
	this one - by the time this runs (``after_migrate``, well after schema
	sync), it no longer exists to read directly. Its data was stashed into
	cache by ``prymage/patches/stash_legacy_doctype_sharing.py``, a
	``pre_model_sync`` patch that ran *before* schema sync dropped it - see
	that file for why the handoff has to go through cache rather than being
	read directly here.

	Only ever adds rows that are not already there, so it is safe to run on
	every migrate even after the stash is long gone (its own cache key has a
	1-hour expiry) or was never there to begin with (a fresh install).
	"""
	import json

	from prymage.patches.stash_legacy_doctype_sharing import STASH_CACHE_KEY

	stashed = frappe.cache.get_value(STASH_CACHE_KEY)
	if not stashed:
		return False

	legacy_rows = json.loads(stashed)
	existing_pairs = {(row.company, row.document_type) for row in doc.company_doctype_sharing}
	changed = False

	for legacy_row in legacy_rows:
		for share_field, doctypes in LEGACY_SHARE_FIELD_TO_DOCTYPES.items():
			is_shared = bool(legacy_row.get(share_field))
			for document_type in doctypes:
				pair = (legacy_row["company"], document_type)
				if pair in existing_pairs:
					continue
				doc.append(
					"company_doctype_sharing",
					{"company": legacy_row["company"], "document_type": document_type, "is_shared": is_shared},
				)
				existing_pairs.add(pair)
				changed = True

	if changed:
		frappe.cache.delete_value(STASH_CACHE_KEY)

	return changed


# ---------------------------------------------------------------------------
def seed_subscription_settings():
	"""Seed field defaults one by one.

	Field-by-field rather than wholesale, so a setting added in a later release
	picks up its default on an existing site without overwriting anything the
	client has already changed.
	"""
	doc = frappe.get_single("Prymage Subscription Settings")
	meta = frappe.get_meta("Prymage Subscription Settings")

	changed = False
	for df in meta.fields:
		if df.fieldtype in ("Section Break", "Column Break", "HTML", "Tab Break"):
			continue
		if df.default in (None, ""):
			continue
		if doc.get(df.fieldname) in (None, ""):
			doc.set(df.fieldname, df.default)
			changed = True

	if changed:
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		print("Prymage: subscription settings defaults seeded")


# ---------------------------------------------------------------------------
def add_switch_company_to_navbar():
	"""A "Switch Company" entry under the avatar menu (requirement A1).

	The requirement is that a user can change company "from the same screen at
	any time without logging out again", so the selection screen needs a way in
	from everywhere. Navbar Settings is the standard place for it; the condition
	hides the item when company selection is switched off.
	"""
	settings = frappe.get_single("Navbar Settings")

	for row in settings.settings_dropdown:
		if row.item_label == NAVBAR_ITEM_LABEL:
			# Keep the action current without touching `hidden`, which an
			# administrator may have deliberately set.
			if row.item_type != "Action" or row.action != NAVBAR_ACTION or not row.icon:
				row.item_type = "Action"
				row.action = NAVBAR_ACTION
				row.route = None
				row.icon = row.icon or "organization"
				settings.save(ignore_permissions=True)
			return

	idx = 0
	for row in settings.settings_dropdown:
		if row.item_type == "Separator":
			idx = row.idx
			break

	settings.append(
		"settings_dropdown",
		{
			"item_label": NAVBAR_ITEM_LABEL,
			"item_type": "Action",
			"action": NAVBAR_ACTION,
			"icon": "organization",
			"is_standard": 0,
			"hidden": 0,
			"condition": (
				"frappe.boot.prymage_multi_company && "
				"frappe.boot.prymage_multi_company.require_selection && "
				"frappe.boot.prymage_multi_company.allowed_companies.length > 1"
			),
		},
	)

	# Put it above the first separator so it sits with the user's own actions.
	if idx:
		new_row = settings.settings_dropdown[-1]
		settings.settings_dropdown.remove(new_row)
		settings.settings_dropdown.insert(idx - 1, new_row)
		for position, row in enumerate(settings.settings_dropdown, start=1):
			row.idx = position

	settings.save(ignore_permissions=True)
	print("Prymage: Switch Company added to the navbar")
