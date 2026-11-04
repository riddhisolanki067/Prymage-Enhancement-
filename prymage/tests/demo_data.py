"""Fixtures for the verification run.

Everything created here is prefixed ``PRYMAGE TEST`` / ``_PT`` so it can be
told apart from the client's own data at a glance, and ``reset()`` removes it
again. The payroll data already on this site is never touched.
"""

import frappe
from frappe.utils import getdate, nowdate

COMPANY_A = "PRYMAGE TEST Alpha"
COMPANY_B = "PRYMAGE TEST Beta"
ABBR_A = "PTA"
ABBR_B = "PTB"

USER_A = "prymage-test-alpha@example.com"
USER_B = "prymage-test-beta@example.com"
USER_BOTH = "prymage-test-both@example.com"

CUSTOMER_A = "PRYMAGE TEST Customer Alpha"
CUSTOMER_B = "PRYMAGE TEST Customer Beta"
CUSTOMER_SHARED = "PRYMAGE TEST Customer Shared"
SUPPLIER_A = "PRYMAGE TEST Supplier Alpha"
SUPPLIER_B = "PRYMAGE TEST Supplier Beta"
ITEM_SERVICE = "_PT-SERVICE"

WHT_CATEGORY = "PRYMAGE TEST WHT 7.5"
WHT_RATE = 7.5
VAT_RATE = 20.0

CURRENCY = "GHS"


# ---------------------------------------------------------------------------
def build():
	"""Idempotent: safe to run repeatedly."""
	frappe.flags.in_prymage_test = True
	companies = [_company(COMPANY_A, ABBR_A), _company(COMPANY_B, ABBR_B)]
	_fiscal_year()
	_letter_heads()
	_item()
	for company in companies:
		_wht_category(company)
	_parties()
	_users()
	frappe.db.commit()
	return {"companies": companies}


def letter_head_for(company) -> str:
	"""One letterhead per test company, so D4 can be proved rather than assumed."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	return f"PRYMAGE TEST Letterhead {abbr}"


def _letter_heads():
	for company in (COMPANY_A, COMPANY_B):
		name = letter_head_for(company)
		if not frappe.db.exists("Letter Head", name):
			frappe.get_doc(
				{
					"doctype": "Letter Head",
					"letter_head_name": name,
					"content": f"<p><b>{company}</b></p>",
					"is_default": 0,
					"disabled": 0,
					"source": "HTML",
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Company", company, "default_letter_head", name)


def _company(name, abbr):
	if frappe.db.exists("Company", name):
		return name

	doc = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": name,
			"abbr": abbr,
			"default_currency": CURRENCY,
			"country": "Ghana",
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _fiscal_year():
	today = getdate(nowdate())
	year = str(today.year)
	if frappe.db.exists("Fiscal Year", year):
		return year

	frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": year,
			"year_start_date": f"{year}-01-01",
			"year_end_date": f"{year}-12-31",
		}
	).insert(ignore_permissions=True)
	return year


def _item():
	if frappe.db.exists("Item", ITEM_SERVICE):
		return ITEM_SERVICE

	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": ITEM_SERVICE,
			"item_name": "PRYMAGE TEST Service",
			"item_group": _item_group(),
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"is_purchase_item": 1,
			"is_sales_item": 1,
		}
	).insert(ignore_permissions=True)
	return ITEM_SERVICE


def _item_group():
	for candidate in ("Services", "All Item Groups"):
		if frappe.db.exists("Item Group", candidate):
			return candidate
	return frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]


def _wht_category(company):
	"""A 7.5% category deducting on Net Total - VAT excluded by design.

	`rates` and `accounts` are mandatory, so the first insert has to carry both.
	"""
	year = str(getdate(nowdate()).year)
	rate_row = {
		"from_date": f"{year}-01-01",
		"to_date": f"{year}-12-31",
		"tax_withholding_rate": WHT_RATE,
		"single_threshold": 0,
		"cumulative_threshold": 0,
	}

	if not frappe.db.exists("Tax Withholding Category", WHT_CATEGORY):
		frappe.get_doc(
			{
				"doctype": "Tax Withholding Category",
				"__newname": WHT_CATEGORY,
				"category_name": WHT_CATEGORY,
				"tax_deduction_basis": "Net Total",
				"round_off_tax_amount": 0,
				"rates": [rate_row],
				"accounts": [{"company": company, "account": _wht_account(company)}],
			}
		).insert(ignore_permissions=True)
		return WHT_CATEGORY

	doc = frappe.get_doc("Tax Withholding Category", WHT_CATEGORY)
	changed = False

	if not any(row.from_date and str(row.from_date).startswith(year) for row in doc.rates):
		doc.append("rates", rate_row)
		changed = True

	if not any(row.company == company for row in doc.accounts):
		doc.append("accounts", {"company": company, "account": _wht_account(company)})
		changed = True

	if changed:
		doc.save(ignore_permissions=True)

	return WHT_CATEGORY


def _wht_account(company):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"PRYMAGE TEST Withholding Tax - {abbr}"
	if frappe.db.exists("Account", name):
		return name

	parent = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Tax", "is_group": 1},
		"name",
	) or frappe.db.get_value(
		"Account", {"company": company, "root_type": "Liability", "is_group": 1}, "name"
	)

	frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": f"PRYMAGE TEST Withholding Tax",
			"parent_account": parent,
			"company": company,
			"root_type": "Liability",
			"account_type": "Tax",
			"is_group": 0,
		}
	).insert(ignore_permissions=True)
	return name


def _parties():
	group = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
	territory = frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0]
	supplier_group = frappe.get_all("Supplier Group", filters={"is_group": 0}, pluck="name", limit=1)[0]

	for name, company in (
		(CUSTOMER_A, COMPANY_A),
		(CUSTOMER_B, COMPANY_B),
		(CUSTOMER_SHARED, None),
	):
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": group,
					"territory": territory,
					"custom_owner_company": company,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Customer", name, "custom_owner_company", company)

	for name, company in ((SUPPLIER_A, COMPANY_A), (SUPPLIER_B, COMPANY_B)):
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": name,
					"supplier_group": supplier_group,
					"custom_owner_company": company,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Supplier", name, "custom_owner_company", company)


def _users():
	for email, companies in (
		(USER_A, [COMPANY_A]),
		(USER_B, [COMPANY_B]),
		(USER_BOTH, [COMPANY_A, COMPANY_B]),
	):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
					"user_type": "System User",
					"roles": [{"role": "Accounts User"}, {"role": "Accounts Manager"}],
				}
			)
			user.insert(ignore_permissions=True)

		user = frappe.get_doc("User", email)
		user.set("custom_allowed_companies", [])
		for index, company in enumerate(companies):
			user.append("custom_allowed_companies", {"company": company, "is_default": index == 0})
		user.save(ignore_permissions=True)


# ---------------------------------------------------------------------------
def reset():
	"""Remove every fixture. Destructive, test data only."""
	frappe.flags.in_prymage_test = True

	# Statements first: their entry rows point at the vouchers, and although
	# `ignore_links_on_delete` lets the vouchers go, removing the statements
	# first keeps the order of operations obvious.
	for name in frappe.get_all("Prymage Ledger Statement", pluck="name"):
		frappe.delete_doc("Prymage Ledger Statement", name, force=True, ignore_permissions=True)

	for doctype in (
		"Payment Entry",
		"Purchase Invoice",
		"Sales Invoice",
		"Journal Entry",
	):
		for name in frappe.get_all(
			doctype, filters={"company": ("in", [COMPANY_A, COMPANY_B])}, pluck="name"
		):
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.cancel()
			# force: a cancelled voucher keeps its GL Entry rows, and those
			# links would otherwise block the delete.
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

	# Remove the ledger rows those vouchers left behind, so nothing dangles.
	for doctype in (
		"GL Entry",
		"Payment Ledger Entry",
		"Advance Payment Ledger Entry",
		"Tax Withholding Entry",
	):
		if frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).get_field("company"):
			frappe.db.delete(doctype, {"company": ("in", [COMPANY_A, COMPANY_B])})

	for email in (USER_A, USER_B, USER_BOTH):
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)

	for doctype, names in (
		("Customer", [CUSTOMER_A, CUSTOMER_B, CUSTOMER_SHARED]),
		("Supplier", [SUPPLIER_A, SUPPLIER_B]),
		("Item", [ITEM_SERVICE]),
		("Tax Withholding Category", [WHT_CATEGORY]),
	):
		for name in names:
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

	for company in (COMPANY_A, COMPANY_B):
		if frappe.db.exists("Company", company):
			name = letter_head_for(company)
			frappe.db.set_value("Company", company, "default_letter_head", None)
			frappe.delete_doc("Company", company, force=True, ignore_permissions=True)
			if frappe.db.exists("Letter Head", name):
				frappe.delete_doc("Letter Head", name, force=True, ignore_permissions=True)

	frappe.db.commit()
	print("Prymage test fixtures removed")
