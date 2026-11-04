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
# A ledger statement is a snapshot report. Its entry rows carry a Dynamic Link
# to the voucher so a user can click through, but without this hook that link
# would make Frappe refuse to delete or cancel any voucher that appears on a
# saved statement - a report must never hold the ledger hostage.
# ---------------------------------------------------------------------------
ignore_links_on_delete = ["Prymage Ledger Statement"]

# ---------------------------------------------------------------------------
# A5 - visibility rules for shared master data
# ---------------------------------------------------------------------------
permission_query_conditions = {
	"Customer": "prymage.multi_company.permissions.customer_query_conditions",
	"Supplier": "prymage.multi_company.permissions.supplier_query_conditions",
	"Item": "prymage.multi_company.permissions.item_query_conditions",
	"Price List": "prymage.multi_company.permissions.price_list_query_conditions",
	"Contact": "prymage.multi_company.permissions.contact_query_conditions",
	"Address": "prymage.multi_company.permissions.address_query_conditions",
}

has_permission = {
	"Customer": "prymage.multi_company.permissions.has_permission",
	"Supplier": "prymage.multi_company.permissions.has_permission",
	"Item": "prymage.multi_company.permissions.has_permission",
	"Price List": "prymage.multi_company.permissions.has_permission",
	"Contact": "prymage.multi_company.permissions.has_permission",
	"Address": "prymage.multi_company.permissions.has_permission",
	"Prymage Subscription Settings": "prymage.subscription.guard.settings_has_permission",
}

# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------
doc_events = {
	"Customer": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Supplier": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Item": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Price List": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Contact": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
	"Address": {"before_insert": "prymage.multi_company.tagging.set_owner_company"},
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
	"Payment Entry": {
		"validate": "prymage.overrides.payment_entry.validate_vat_exclusion",
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
