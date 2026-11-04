"""One-command setup and teardown for manual UAT.

    bench --site <site> execute prymage.tests.uat.setup       # build the test bed
    bench --site <site> execute prymage.tests.uat.state       # show current settings
    bench --site <site> execute prymage.tests.uat.teardown    # remove everything

Creates two extra companies, three logins, parties, an item, a withholding
category, a letterhead per company, and a few posted transactions - everything
the 17 test scripts need and nothing more.

Company access is deliberately left **unassigned**: assigning it is test
UAT-A2-01, and the login screen test depends on the tester having done it.

Test data only. ``teardown`` deletes every fixture and restores the settings,
so never point this at a site holding real data.
"""

import frappe
from frappe.utils import add_days, flt, nowdate

from prymage.prymage.doctype.prymage_multi_company_settings import (
	prymage_multi_company_settings as mcs,
)
from prymage.prymage.doctype.prymage_subscription_settings import (
	prymage_subscription_settings as subs,
)
from prymage.tests import demo_data as dd

#: Deliberately short and easy to TYPE. Pasting into the login form invokes
#: Chrome's password manager, which stalls the tab on a loaded machine; typing
#: eight characters avoids the clipboard path entirely.
PASSWORD = "Uat@2026"

LOGINS = (
	(dd.USER_A, [dd.COMPANY_A], "one company"),
	(dd.USER_B, [dd.COMPANY_B], "one company, the other one"),
	(dd.USER_BOTH, [dd.COMPANY_A, dd.COMPANY_B], "both companies"),
)


def setup():
	dd.build()

	settings = frappe.get_single("Prymage Multi Company Settings")
	if settings.sync_companies():
		settings.save(ignore_permissions=True)

	# Start every run from the shipped defaults, so a half-finished previous
	# round cannot make a test pass or fail for the wrong reason.
	settings = frappe.get_single("Prymage Multi Company Settings")
	settings.enable_company_data_isolation = 0
	settings.treat_untagged_as_shared = 1
	settings.require_company_selection_on_login = 1
	settings.skip_selection_for_single_company = 1
	settings.allow_multiple_company_selection = 1
	for row in settings.company_data_sharing:
		row.share_customers = 1
		row.share_suppliers = 1
		row.share_items = 1
		row.share_price_lists = 1
		row.share_contacts_and_addresses = 1
	settings.save(ignore_permissions=True)
	mcs.clear_settings_cache()

	sub = frappe.get_single("Prymage Subscription Settings")
	sub.restrict_access_after_expiry = 0
	sub.save(ignore_permissions=True)
	sub = frappe.get_single("Prymage Subscription Settings")
	sub.expiry_date = None
	sub.provider_name = "Prymage ERP Services"
	sub.provider_contact = "support@prymage.example"
	sub.reminder_days = 30
	sub.save(ignore_permissions=True)
	subs.clear_subscription_cache()

	_set_passwords()
	_clear_company_access()
	_transactions()

	frappe.db.commit()
	state()


def _set_passwords():
	for email, _companies, _note in LOGINS:
		user = frappe.get_doc("User", email)
		user.new_password = PASSWORD
		user.save(ignore_permissions=True)


def _clear_company_access():
	"""Assigning company access is test UAT-A2-01 - leave it for the tester."""
	for email, _companies, _note in LOGINS:
		user = frappe.get_doc("User", email)
		user.set("custom_allowed_companies", [])
		user.save(ignore_permissions=True)


def _transactions():
	"""A handful of posted documents so the statements have something to show.

	Dates are spread on purpose: one invoice sits *before* the period the test
	script asks for, so the statement's "Balance brought forward" line has a
	real figure in it rather than zero.

	``set_posting_time`` has to be on, otherwise ERPNext silently resets the
	posting date of a back-dated invoice to today.
	"""
	company = dd.COMPANY_A

	if not frappe.db.exists(
		"Sales Invoice", {"company": company, "customer": dd.CUSTOMER_SHARED, "docstatus": 1}
	):
		income = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
		)

		# Before the test period -> becomes the opening balance.
		opening_si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": company,
				"customer": dd.CUSTOMER_SHARED,
				"set_posting_time": 1,
				"posting_date": add_days(nowdate(), -75),
				"due_date": add_days(nowdate(), -45),
				"currency": dd.CURRENCY,
				"items": [
					{
						"item_code": dd.ITEM_SERVICE,
						"qty": 1,
						"rate": 1500,
						"income_account": income,
					}
				],
			}
		)
		opening_si.insert(ignore_permissions=True)
		opening_si.submit()

		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": company,
				"customer": dd.CUSTOMER_SHARED,
				"set_posting_time": 1,
				"posting_date": add_days(nowdate(), -20),
				"due_date": add_days(nowdate(), 10),
				"currency": dd.CURRENCY,
				"items": [
					{
						"item_code": dd.ITEM_SERVICE,
						"qty": 1,
						"rate": 5000,
						"income_account": income,
					}
				],
			}
		)
		si.insert(ignore_permissions=True)
		si.submit()

		paid_to = _account(company, "Bank") or _account(company, "Cash")
		pe = frappe.get_doc(
			{
				"doctype": "Payment Entry",
				"payment_type": "Receive",
				"company": company,
				"posting_date": add_days(nowdate(), -10),
				"party_type": "Customer",
				"party": dd.CUSTOMER_SHARED,
				"paid_from": _account(company, "Receivable"),
				"paid_to": paid_to,
				"paid_amount": 2000,
				"received_amount": 2000,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"reference_no": "CHQ-100345",
				"reference_date": add_days(nowdate(), -10),
				"mode_of_payment": "Cheque",
				"references": [
					{
						"reference_doctype": "Sales Invoice",
						"reference_name": si.name,
						"allocated_amount": 2000,
					}
				],
			}
		)
		pe.insert(ignore_permissions=True)
		pe.submit()

	# A purchase invoice carrying VAT and withholding tax, for the C scripts
	# and for the supplier statement.
	if not frappe.db.exists(
		"Purchase Invoice", {"company": company, "supplier": dd.SUPPLIER_A, "docstatus": 1}
	):
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": company,
				"supplier": dd.SUPPLIER_A,
				"set_posting_time": 1,
				"posting_date": add_days(nowdate(), -15),
				"due_date": add_days(nowdate(), 15),
				"currency": dd.CURRENCY,
				"apply_tds": 1,
				"tax_withholding_category": dd.WHT_CATEGORY,
				"items": [
					{
						"item_code": dd.ITEM_SERVICE,
						"qty": 1,
						"rate": 10000,
						"apply_tds": 1,
						"tax_withholding_category": dd.WHT_CATEGORY,
					}
				],
			}
		)
		pi.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": _vat_account(company),
				"description": "VAT",
				"rate": dd.VAT_RATE,
				"category": "Total",
				"add_deduct_tax": "Add",
			},
		)
		pi.insert(ignore_permissions=True)
		pi.submit()


def _account(company, account_type):
	return frappe.db.get_value(
		"Account", {"company": company, "account_type": account_type, "is_group": 0}, "name"
	)


def _vat_account(company):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"PRYMAGE TEST VAT - {abbr}"
	if frappe.db.exists("Account", name):
		return name

	parent = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Tax", "is_group": 1}, "name"
	) or frappe.db.get_value(
		"Account", {"company": company, "root_type": "Liability", "is_group": 1}, "name"
	)
	frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": "PRYMAGE TEST VAT",
			"parent_account": parent,
			"company": company,
			"root_type": "Liability",
			"account_type": "Tax",
			"is_group": 0,
		}
	).insert(ignore_permissions=True)
	return name


def state():
	settings = frappe.get_single("Prymage Multi Company Settings")
	sub = frappe.get_single("Prymage Subscription Settings")

	print()
	print("=" * 70)
	print("PRYMAGE UAT TEST BED")
	print("=" * 70)
	print(f"Open the site at  http://localhost:{frappe.conf.webserver_port or 8000}/login")
	print("(start it with `bench serve --port %s` if it is not running)" % (frappe.conf.webserver_port or 8000))
	print()
	print("LOGINS   password for all three:", PASSWORD)
	for email, companies, note in LOGINS:
		assigned = frappe.get_all(
			"User Permission",
			filters={"user": email, "allow": "Company"},
			pluck="for_value",
		)
		print(f"  {email:40s} should end up with {note}")
		print(f"      currently assigned: {assigned or 'nothing yet (that is test UAT-A2-01)'}")
	print()
	print("COMPANIES")
	for c in frappe.get_all(
		"Company",
		fields=["name", "abbr", "default_currency", "default_letter_head"],
		order_by="name",
	):
		print(f"  {c.name:28s} {c.abbr:5s} {c.default_currency}  letterhead: {c.default_letter_head}")
	print()
	print("MASTER RECORDS (note the company tag - the A5 scripts depend on it)")
	for dt in ("Customer", "Supplier"):
		for row in frappe.get_all(
			dt,
			filters={"name": ("like", "PRYMAGE TEST%")},
			fields=["name", "custom_owner_company"],
			order_by="name",
		):
			print(f"  {dt:9s} {row.name:36s} owned by: {row.custom_owner_company or '(blank = shared)'}")
	print(f"  Item      {dd.ITEM_SERVICE}")
	print()
	print("POSTED DOCUMENTS")
	for dt in ("Sales Invoice", "Purchase Invoice", "Payment Entry"):
		for row in frappe.get_all(
			dt,
			filters={"company": ("in", [dd.COMPANY_A, dd.COMPANY_B]), "docstatus": 1},
			fields=["name", "posting_date", "grand_total" if dt != "Payment Entry" else "paid_amount"],
			order_by="posting_date",
		):
			amount = row.get("grand_total") or row.get("paid_amount")
			print(f"  {dt:17s} {row.name:28s} {row.posting_date}  {flt(amount):>12,.2f}")
	print()
	print("WITHHOLDING")
	for row in frappe.get_all(
		"Tax Withholding Category",
		filters={"name": dd.WHT_CATEGORY},
		fields=["name", "tax_deduction_basis"],
	):
		rates = frappe.get_all(
			"Tax Withholding Rate",
			filters={"parent": row.name},
			fields=["tax_withholding_rate", "from_date", "to_date"],
		)
		print(f"  {row.name}  basis: {row.tax_deduction_basis}  rate: {rates[0].tax_withholding_rate if rates else '?'}%")
	print(f"  Test VAT rate to type on the Payment Entry: {dd.VAT_RATE}%")
	print()
	print("SETTINGS RIGHT NOW (setup leaves them at the shipped defaults)")
	print(f"  Data isolation                : {'ON' if settings.enable_company_data_isolation else 'OFF'}")
	print(f"  Treat untagged as shared      : {'yes' if settings.treat_untagged_as_shared else 'no'}")
	print(f"  Require selection on login    : {'yes' if settings.require_company_selection_on_login else 'no'}")
	print(f"  Skip screen for single company: {'yes' if settings.skip_selection_for_single_company else 'no'}")
	print(f"  Allow selecting multiple      : {'yes' if settings.allow_multiple_company_selection else 'no'}")
	print("  Sharing per company:")
	for row in settings.company_data_sharing:
		print(
			f"    {row.company:28s} cust={row.share_customers} supp={row.share_suppliers} "
			f"item={row.share_items} price={row.share_price_lists} contact={row.share_contacts_and_addresses}"
		)
	print(f"  Subscription expiry           : {sub.expiry_date or '(not set)'}")
	print(f"  Reminder starts (days before) : {sub.reminder_days}")
	print(f"  Restrict access after expiry  : {'ON' if sub.restrict_access_after_expiry else 'OFF'}")
	print(f"  site_config safety release    : {frappe.conf.get(subs.BYPASS_KEY) or '(not set)'}")
	print("=" * 70)
	print()


def teardown():
	sub = frappe.get_single("Prymage Subscription Settings")
	sub.restrict_access_after_expiry = 0
	sub.save(ignore_permissions=True)
	sub = frappe.get_single("Prymage Subscription Settings")
	sub.expiry_date = None
	sub.provider_name = None
	sub.provider_contact = None
	sub.save(ignore_permissions=True)
	subs.clear_subscription_cache()

	dd.reset()

	settings = frappe.get_single("Prymage Multi Company Settings")
	if settings.sync_companies():
		settings.save(ignore_permissions=True)
	settings = frappe.get_single("Prymage Multi Company Settings")
	settings.enable_company_data_isolation = 0
	settings.treat_untagged_as_shared = 1
	settings.require_company_selection_on_login = 1
	settings.skip_selection_for_single_company = 1
	settings.allow_multiple_company_selection = 1
	settings.save(ignore_permissions=True)
	mcs.clear_settings_cache()

	frappe.db.commit()
	print("Prymage UAT test bed removed; settings back to the shipped defaults.")
