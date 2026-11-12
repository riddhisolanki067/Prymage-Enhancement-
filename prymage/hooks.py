app_name = "prymage"
app_title = "Prymage"
app_publisher = "Riddhi"
app_description = (
	"ERPNext enhancements for Prymage: multi-company management, subscription expiry "
	"control, withholding tax on net of VAT, and ledger statement print formats"
)
app_email = "riddhisolanki067@gmail.com"
app_license = "mit"
required_apps = ["frappe/erpnext"]

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
app_include_js = "prymage.bundle.js"

# ---------------------------------------------------------------------------
# Install / migrate
# ---------------------------------------------------------------------------
after_install = "prymage.install.after_install"
after_migrate = "prymage.install.after_migrate"

# ---------------------------------------------------------------------------
# Boot + session
# ---------------------------------------------------------------------------
extend_bootinfo = "prymage.boot.extend_bootinfo"

on_session_creation = [
	"prymage.subscription.guard.on_session_creation",
	"prymage.multi_company.session.on_session_creation",
]

on_logout = "prymage.multi_company.session.on_logout"

# ---------------------------------------------------------------------------
# D - the 3 ledger-statement print formats. A Report-bound Print Format is
# forced to standard="No" by Frappe's own PrintFormat.before_save() the
# moment it is saved, so it cannot be managed the normal module-folder
# standard="Yes" way (reload_doc skips non-standard documents entirely).
# Shipped as a fixture instead - idempotently re-synced on every migrate.
# ---------------------------------------------------------------------------
fixtures = [
	{
		"doctype": "Print Format",
		"filters": [
			[
				"name",
				"in",
				(
					"Prymage Supplier Ledger Statement",
					"Prymage Customer Ledger Statement",
					"Prymage Account Ledger Statement",
				),
			]
		],
	}
]

# ---------------------------------------------------------------------------
# A5 - visibility rules for shared master data.
#
# Registered under the "*" wildcard doctype key, not one entry per doctype:
# both permission_query_conditions and has_permission dispatch
# hooks.get(doctype, []) + hooks.get("*", []) (verified directly in
# frappe/model/db_query.py and frappe/permissions.py), so one wildcard
# handler runs for every doctype's queries/permission checks, and the actual
# gate is the "Shareable DocTypes" table on Prymage Multi Company Settings -
# a cached, in-memory membership check (is_shareable()), not this file. This
# is the whole point: adding a DocType to that table is the only thing that
# ever needs to happen to bring a new doctype into company data isolation -
# nothing here changes, ever again, for any doctype.
# ---------------------------------------------------------------------------
permission_query_conditions = {
	"*": "prymage.multi_company.permissions.query_conditions",
}

has_permission = {
	"*": "prymage.multi_company.permissions.has_permission",
	"Prymage Subscription Settings": "prymage.subscription.guard.settings_has_permission",
}

# ---------------------------------------------------------------------------
# Document events
#
# "*" here too: set_owner_company() is called on every insert in the system
# and is_shareable() (one cached set lookup, no query) is the first thing it
# does - cheap enough to be the same "no code change needed" story as above.
# ---------------------------------------------------------------------------
doc_events = {
	"*": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Company": {
		"on_trash": (
			"prymage.prymage.doctype.prymage_multi_company_settings."
			"prymage_multi_company_settings.on_company_trash"
		)
	},
	"User": {
		"onload": "prymage.multi_company.user_companies.load_allowed_companies",
		"on_update": "prymage.multi_company.user_companies.sync_allowed_companies",
	},
	"Purchase Invoice": {
		"validate": "prymage.overrides.purchase_invoice.validate_withholding_basis",
	},
}

# ---------------------------------------------------------------------------
# C - withholding tax computed on the amount excluding VAT
#
# ERPNext calls PaymentTaxWithholding(self).on_validate() inline inside
# PaymentEntry.validate(), so there is no hook to swap the controller and no
# way to copy that method without copying 25 lines of ERPNext that move between
# versions. We add a mixin in front of the controller instead and re-run our
# own withholding controller after the standard one has finished.
#
# extend_doctype_class, not override_doctype_class: hrms already overrides
# Payment Entry (hrms.overrides.employee_payment_entry.EmployeePaymentEntry)
# and override_doctype_class keeps only the last app's class, which would
# silently drop whichever app lost. An extension is mixed into the MRO *ahead*
# of whatever controller is in force, so both apps keep working and
# super().validate() still reaches hrms and then ERPNext.
# ---------------------------------------------------------------------------
extend_doctype_class = {
	"Payment Entry": "prymage.overrides.payment_entry.PrymagePaymentEntryExtension"
}
