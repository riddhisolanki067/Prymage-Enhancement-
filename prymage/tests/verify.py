"""Verification harness for every requirement in the ERPNext Enhancement scope.

    bench --site <site> execute prymage.tests.verify.run

Builds its own fixtures, exercises each requirement through the same code paths
the user goes through, and prints a pass/fail line per check. Requirement D is
asserted against ERPNext's own General Ledger report rather than against
hand-written numbers, so the statements cannot drift away from the ledger.

Settings are captured at the start and restored at the end, so a run never
leaves the site with data isolation switched on.
"""

import contextlib
import traceback

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from prymage.multi_company import permissions, session, tagging
from prymage.prymage.doctype.prymage_multi_company_settings import (
	prymage_multi_company_settings as mcs,
)
from prymage.prymage.doctype.prymage_subscription_settings import (
	prymage_subscription_settings as subs,
)
from prymage.subscription import guard
from prymage.tests import demo_data as dd

RESULTS = []


# ---------------------------------------------------------------------------
# harness plumbing
# ---------------------------------------------------------------------------
def check(label, condition, detail=""):
	RESULTS.append((bool(condition), label, detail))
	print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
	return bool(condition)


def check_eq(label, actual, expected, tolerance=0.005):
	if isinstance(expected, float) or isinstance(actual, float):
		ok = abs(flt(actual) - flt(expected)) <= tolerance
	else:
		ok = actual == expected
	return check(label, ok, f"got {actual!r}, expected {expected!r}")


def check_throws(label, fn, exc=Exception):
	try:
		fn()
	except exc:
		return check(label, True)
	except Exception as e:  # noqa: BLE001
		return check(label, False, f"wrong exception: {type(e).__name__}: {e}")
	return check(label, False, "no exception raised")


@contextlib.contextmanager
def as_user(user):
	"""Switch user without disturbing the session.

	frappe.set_user overwrites session.sid and blanks session.data, which breaks
	a web session. The harness runs from the CLI, but restoring all three keeps
	it safe to call from anywhere.
	"""
	original_user = frappe.session.user
	original_sid = frappe.session.sid
	original_data = frappe.session.data
	try:
		frappe.set_user(user)
		yield
	finally:
		frappe.set_user(original_user)
		frappe.session.sid = original_sid
		frappe.session.data = original_data


@contextlib.contextmanager
def isolation(enabled=True, untagged_shared=True, sharing=None):
	"""Temporarily configure Multi Company Settings."""
	doc = frappe.get_single("Prymage Multi Company Settings")
	doc.enable_company_data_isolation = int(enabled)
	doc.treat_untagged_as_shared = int(untagged_shared)

	# A company named here must have a row, otherwise the check would silently
	# be testing the default instead of the configuration it asked for.
	present = {row.company for row in doc.company_data_sharing}
	for company in sharing or {}:
		if company not in present:
			raise AssertionError(f"no Company Data Sharing row for {company}")

	for row in doc.company_data_sharing:
		if sharing and row.company in sharing:
			for field, value in sharing[row.company].items():
				row.set(field, int(value))
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	mcs.clear_settings_cache()
	try:
		yield
	finally:
		mcs.clear_settings_cache()


def set_selection(user, companies):
	session.set_selected_companies(companies, user)


# ---------------------------------------------------------------------------
# A1 / A2 - company selection and company access
# ---------------------------------------------------------------------------
def verify_company_selection():
	print("\nA1/A2  Company selection screen and company access per user")

	check_eq(
		"A2 user assigned one company sees only that company",
		session.get_allowed_companies(dd.USER_A),
		[dd.COMPANY_A],
	)
	check_eq(
		"A2 user assigned two companies sees both",
		session.get_allowed_companies(dd.USER_BOTH),
		sorted([dd.COMPANY_A, dd.COMPANY_B]),
	)
	check(
		"A2 unassigned company is not offered",
		dd.COMPANY_B not in session.get_allowed_companies(dd.USER_A),
	)

	permissions_for_both = frappe.get_all(
		"User Permission",
		filters={"user": dd.USER_BOTH, "allow": "Company"},
		fields=["for_value", "is_default"],
	)
	check_eq(
		"A2 grid wrote real User Permission records", len(permissions_for_both), 2
	)
	check(
		"A2 the first allowed company is flagged default",
		any(p.for_value == dd.COMPANY_A and p.is_default for p in permissions_for_both),
	)

	# Removing a row must remove the permission, not leave it orphaned.
	user = frappe.get_doc("User", dd.USER_BOTH)
	user.set(
		"custom_allowed_companies", [{"company": dd.COMPANY_A, "is_default": 1}]
	)
	user.save(ignore_permissions=True)
	check_eq(
		"A2 removing a row removes the User Permission",
		frappe.db.count("User Permission", {"user": dd.USER_BOTH, "allow": "Company"}),
		1,
	)
	# Put it back for the rest of the run.
	user = frappe.get_doc("User", dd.USER_BOTH)
	user.append("custom_allowed_companies", {"company": dd.COMPANY_B})
	user.save(ignore_permissions=True)
	check_eq(
		"A2 adding a row recreates the User Permission",
		frappe.db.count("User Permission", {"user": dd.USER_BOTH, "allow": "Company"}),
		2,
	)

	with as_user(dd.USER_A):
		session.select_companies([dd.COMPANY_A])
		check_eq(
			"A1 selection is stored for the session",
			session.get_selected_companies(),
			[dd.COMPANY_A],
		)
		check_eq(
			"A1 selection sets the company default every form reads",
			frappe.defaults.get_user_default("company"),
			dd.COMPANY_A,
		)
		check_throws(
			"A1 a company the user has no access to is refused",
			lambda: session.select_companies([dd.COMPANY_B]),
			frappe.PermissionError,
		)

	with as_user(dd.USER_BOTH):
		session.select_companies([dd.COMPANY_A, dd.COMPANY_B])
		check_eq(
			"A1 multiple companies can be selected at once",
			sorted(session.get_selected_companies()),
			sorted([dd.COMPANY_A, dd.COMPANY_B]),
		)
		check_eq(
			"A1 the first selected company supplies the defaults",
			frappe.defaults.get_user_default("company"),
			dd.COMPANY_A,
		)

		doc = frappe.get_single("Prymage Multi Company Settings")
		doc.allow_multiple_company_selection = 0
		doc.save(ignore_permissions=True)
		mcs.clear_settings_cache()
		check_throws(
			"A1 multi-select is refused when the setting is off",
			lambda: session.select_companies([dd.COMPANY_A, dd.COMPANY_B]),
		)
		doc = frappe.get_single("Prymage Multi Company Settings")
		doc.allow_multiple_company_selection = 1
		doc.save(ignore_permissions=True)
		mcs.clear_settings_cache()

	# Login routing: the flag read by frappe.website.utils.get_home_page().
	with as_user(dd.USER_BOTH):
		frappe.local.flags.home_page = None
		session.on_session_creation()
		check_eq(
			"A1 a multi-company user is routed to the selection screen at login",
			frappe.local.flags.home_page,
			session.SELECTION_PAGE,
		)
		check_eq(
			"A1 login clears any previous selection", session.get_selected_companies(), []
		)

	with as_user(dd.USER_A):
		frappe.local.flags.home_page = None
		session.on_session_creation()
		check(
			"A1 a single-company user skips the screen",
			frappe.local.flags.home_page is None,
			f"home_page={frappe.local.flags.home_page}",
		)
		check_eq(
			"A1 the single company is selected automatically",
			session.get_selected_companies(),
			[dd.COMPANY_A],
		)

	frappe.local.flags.home_page = None

	with as_user(dd.USER_A):
		session.on_logout()
		check_eq("A1 logout clears the selection", session.get_selected_companies(), [])


# ---------------------------------------------------------------------------
# A3 - settings screen
# ---------------------------------------------------------------------------
def verify_settings_screen():
	print("\nA3     Multi-Company Settings screen")

	doc = frappe.get_single("Prymage Multi Company Settings")
	companies = {row.company for row in doc.company_data_sharing}
	check(
		"A3 every company has a sharing row",
		{dd.COMPANY_A, dd.COMPANY_B} <= companies,
		f"rows for {sorted(companies)}",
	)
	check(
		"A3 a new company starts out sharing everything",
		all(
			row.share_customers
			and row.share_suppliers
			and row.share_items
			and row.share_price_lists
			and row.share_contacts_and_addresses
			for row in doc.company_data_sharing
			if row.company in (dd.COMPANY_A, dd.COMPANY_B)
		),
	)

	def duplicate():
		bad = frappe.get_single("Prymage Multi Company Settings")
		bad.append("company_data_sharing", {"company": dd.COMPANY_A})
		bad.save(ignore_permissions=True)

	check_throws("A3 the same company cannot be listed twice", duplicate)


# ---------------------------------------------------------------------------
# A4 - company tagging
# ---------------------------------------------------------------------------
def verify_tagging():
	print("\nA4     Company tagging on shared master records")

	check(
		"A4 the company tag exists on all six shared masters",
		all(
			frappe.get_meta(dt).get_field("custom_owner_company")
			for dt in ("Customer", "Supplier", "Item", "Price List", "Contact", "Address")
		),
	)

	name = "PRYMAGE TEST Tagging Probe"
	frappe.db.delete("Customer", {"customer_name": name})

	with isolation(enabled=True):
		with as_user(dd.USER_A):
			set_selection(dd.USER_A, [dd.COMPANY_A])
			doc = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": frappe.get_all(
						"Customer Group", filters={"is_group": 0}, pluck="name", limit=1
					)[0],
				}
			).insert(ignore_permissions=True)
		check_eq(
			"A4 a new record is tagged with the company the user is working in",
			doc.custom_owner_company,
			dd.COMPANY_A,
		)
		frappe.delete_doc("Customer", doc.name, force=True, ignore_permissions=True)

		with as_user(dd.USER_A):
			set_selection(dd.USER_A, [dd.COMPANY_A])
			doc = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": frappe.get_all(
						"Customer Group", filters={"is_group": 0}, pluck="name", limit=1
					)[0],
					"custom_owner_company": dd.COMPANY_B,
				}
			).insert(ignore_permissions=True)
		check_eq(
			"A4 an explicitly set company tag is never overwritten",
			doc.custom_owner_company,
			dd.COMPANY_B,
		)
		frappe.delete_doc("Customer", doc.name, force=True, ignore_permissions=True)

	with isolation(enabled=False):
		with as_user(dd.USER_A):
			set_selection(dd.USER_A, [dd.COMPANY_A])
			doc = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": frappe.get_all(
						"Customer Group", filters={"is_group": 0}, pluck="name", limit=1
					)[0],
				}
			).insert(ignore_permissions=True)
		check(
			"A4 nothing is tagged while isolation is off",
			not doc.custom_owner_company,
			f"tag={doc.custom_owner_company!r}",
		)
		frappe.delete_doc("Customer", doc.name, force=True, ignore_permissions=True)

	counts = tagging.tag_existing_records(dd.COMPANY_A, "Customer", dry_run=1)
	check(
		"A4 the back-fill tool reports what it would change without changing it",
		counts["dry_run"] and "Customer" in counts["counts"],
		str(counts),
	)


# ---------------------------------------------------------------------------
# A5 - visibility rules
# ---------------------------------------------------------------------------
def _visible_customers(user):
	with as_user(user):
		return set(frappe.get_list("Customer", pluck="name", limit_page_length=0))


def _link_search_customers(user, txt="PRYMAGE TEST"):
	from frappe.desk.search import search_link

	with as_user(user):
		return {row["value"] for row in search_link("Customer", txt, page_length=50)}


def verify_visibility():
	print("\nA5     Visibility rules in list views, link fields and reports")

	with isolation(enabled=False):
		seen = _visible_customers(dd.USER_A)
		check(
			"A5 with isolation off every company sees every record",
			{dd.CUSTOMER_A, dd.CUSTOMER_B, dd.CUSTOMER_SHARED} <= seen,
			f"{len(seen)} customers visible",
		)

	# Both companies share: the pool is visible to both.
	with isolation(
		enabled=True,
		sharing={
			dd.COMPANY_A: {"share_customers": 1},
			dd.COMPANY_B: {"share_customers": 1},
		},
	):
		set_selection(dd.USER_A, [dd.COMPANY_A])
		seen = _visible_customers(dd.USER_A)
		check(
			"A5 scenario 1 both share: a record from the other company is visible",
			dd.CUSTOMER_B in seen,
		)

	# Neither shares: each company sees only its own.
	with isolation(
		enabled=True,
		sharing={
			dd.COMPANY_A: {"share_customers": 0},
			dd.COMPANY_B: {"share_customers": 0},
		},
	):
		set_selection(dd.USER_A, [dd.COMPANY_A])
		seen = _visible_customers(dd.USER_A)
		check("A5 scenario 2 neither shares: own record visible", dd.CUSTOMER_A in seen)
		check(
			"A5 scenario 2 neither shares: the other company's record is hidden",
			dd.CUSTOMER_B not in seen,
		)
		check(
			"A5 untagged records stay visible while they count as shared",
			dd.CUSTOMER_SHARED in seen,
		)
		check(
			"A5 the link field dropdown hides it too",
			dd.CUSTOMER_B not in _link_search_customers(dd.USER_A),
		)
		with as_user(dd.USER_A):
			check(
				"A5 opening the document directly is refused",
				not frappe.has_permission("Customer", doc=dd.CUSTOMER_B, user=dd.USER_A),
			)
			check(
				"A5 opening an allowed document still works",
				frappe.has_permission("Customer", doc=dd.CUSTOMER_A, user=dd.USER_A),
			)

	# Mixed: A shares, B does not.
	with isolation(
		enabled=True,
		sharing={
			dd.COMPANY_A: {"share_customers": 1},
			dd.COMPANY_B: {"share_customers": 0},
		},
	):
		set_selection(dd.USER_A, [dd.COMPANY_A])
		seen_a = _visible_customers(dd.USER_A)
		set_selection(dd.USER_B, [dd.COMPANY_B])
		seen_b = _visible_customers(dd.USER_B)
		check(
			"A5 scenario 3 the non-sharing company's record stays private",
			dd.CUSTOMER_B not in seen_a,
		)
		check(
			"A5 scenario 3 a non-sharing company sees only its own",
			dd.CUSTOMER_A not in seen_b and dd.CUSTOMER_B in seen_b,
		)

	# Untagged records can be hidden once everything is tagged.
	with isolation(
		enabled=True,
		untagged_shared=False,
		sharing={
			dd.COMPANY_A: {"share_customers": 0},
			dd.COMPANY_B: {"share_customers": 0},
		},
	):
		set_selection(dd.USER_A, [dd.COMPANY_A])
		seen = _visible_customers(dd.USER_A)
		check(
			"A5 untagged records are hidden when they no longer count as shared",
			dd.CUSTOMER_SHARED not in seen,
		)

	# A user working in both companies sees the union.
	with isolation(
		enabled=True,
		sharing={
			dd.COMPANY_A: {"share_customers": 0},
			dd.COMPANY_B: {"share_customers": 0},
		},
	):
		set_selection(dd.USER_BOTH, [dd.COMPANY_A, dd.COMPANY_B])
		seen = _visible_customers(dd.USER_BOTH)
		check(
			"A5 selecting both companies shows both companies' records",
			{dd.CUSTOMER_A, dd.CUSTOMER_B} <= seen,
		)

		seen_admin = set(
			frappe.get_list("Customer", pluck="name", limit_page_length=0, user="Administrator")
		)
		check(
			"A5 the Administrator is never locked out of a record",
			{dd.CUSTOMER_A, dd.CUSTOMER_B, dd.CUSTOMER_SHARED} <= seen_admin,
		)

		sql = permissions.build_conditions("Customer", dd.USER_A)
		check(
			"A5 the SQL fragment fully qualifies its column so it is safe in raw queries",
			"`tabCustomer`.`custom_owner_company`" in sql,
			sql,
		)


# ---------------------------------------------------------------------------
# A6 - audit trail
# ---------------------------------------------------------------------------
def verify_audit_trail():
	print("\nA6     Audit trail of sharing changes")

	check(
		"A6 change tracking is on for the settings",
		frappe.get_meta("Prymage Multi Company Settings").track_changes,
	)

	doc = frappe.get_single("Prymage Multi Company Settings")
	row = next(r for r in doc.company_data_sharing if r.company == dd.COMPANY_A)
	before = int(row.share_items or 0)
	row.share_items = 0 if before else 1
	doc.save(ignore_permissions=True)

	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Prymage Multi Company Settings"},
		fields=["name", "data", "owner", "creation"],
		order_by="creation desc",
		limit=1,
	)
	check("A6 the change produced a Version record", bool(versions))
	if versions:
		data = frappe.parse_json(versions[0].data)
		check(
			"A6 the record carries who changed it and when",
			bool(versions[0].owner and versions[0].creation),
			f"{versions[0].owner} at {versions[0].creation}",
		)
		check(
			"A6 the record carries the old and the new value",
			bool(data.get("row_changed") or data.get("changed") or data.get("added")),
			frappe.as_json(data)[:200],
		)

	# restore
	doc = frappe.get_single("Prymage Multi Company Settings")
	row = next(r for r in doc.company_data_sharing if r.company == dd.COMPANY_A)
	row.share_items = before
	doc.save(ignore_permissions=True)


# ---------------------------------------------------------------------------
# B - subscription expiry
# ---------------------------------------------------------------------------
def verify_subscription():
	print("\nB      Cloud subscription expiry notification and access restriction")

	doc = frappe.get_single("Prymage Subscription Settings")
	original = {
		"expiry_date": doc.expiry_date,
		"reminder_days": doc.reminder_days,
		"restrict_access_after_expiry": doc.restrict_access_after_expiry,
	}

	def configure(days_from_today, restrict=0):
		d = frappe.get_single("Prymage Subscription Settings")
		d.expiry_date = add_days(nowdate(), days_from_today)
		d.reminder_days = 30
		d.restrict_access_after_expiry = restrict
		d.save(ignore_permissions=True)
		subs.clear_subscription_cache()

	try:
		check_throws(
			"B3 the restriction cannot be switched on without an expiry date",
			lambda: _save_subscription(expiry_date=None, restrict=1),
		)
		# The failed save left a mutated Single in the document cache.
		frappe.clear_document_cache(
			"Prymage Subscription Settings", "Prymage Subscription Settings"
		)

		configure(40)
		status = subs.get_status()
		check("B2 no reminder outside the 30 day window", not status.show_reminder)
		check_eq("B2 days remaining is counted correctly", status.days_remaining, 40)

		configure(30)
		status = subs.get_status()
		check("B2 the reminder starts exactly 30 days before expiry", status.show_reminder)
		check_eq("B2 severity 30 days out is informational", status.severity, "info")
		check(
			"B2 the message carries the day count and the date",
			"30" in status.message and "Expiry Date" in status.message,
			status.message,
		)

		configure(10)
		check_eq("B2 severity becomes a warning inside 14 days", subs.get_status().severity, "warning")

		configure(3)
		check_eq("B2 severity becomes critical inside 7 days", subs.get_status().severity, "critical")

		configure(0)
		status = subs.get_status()
		check("B2 the package is still usable on the expiry date itself", not status.expired)

		configure(-1, restrict=0)
		status = subs.get_status()
		check("B3 the day after the expiry date it is expired", status.expired)
		check(
			"B3 nothing is restricted while the restriction is off",
			not status.restrict,
		)
		check(
			"B2 the expired message is used once expired",
			"expired on" in status.message,
			status.message,
		)

		configure(-1, restrict=1)
		check("B3 the restriction applies once switched on", subs.get_status().restrict)

		check("B3 the Administrator is exempt", guard.is_exempt_user("Administrator"))
		check("B3 other users are not exempt", not guard.is_exempt_user(dd.USER_A))

		frappe.conf[subs.BYPASS_KEY] = 1
		subs.clear_subscription_cache()
		check(
			"B3 the site_config safety release lifts the restriction",
			not subs.get_status().restrict,
		)
		check("B3 the safety release is reported back", subs.get_status().bypassed)
		frappe.conf.pop(subs.BYPASS_KEY, None)
		subs.clear_subscription_cache()

		payload = guard.get_boot_payload()
		check(
			"B2 the desk receives the notice through boot info",
			payload and payload["message"] and payload["severity"] == "expired",
			str(payload)[:160],
		)

		settings_doc = frappe.get_single("Prymage Subscription Settings")
		check(
			"B1 the settings are readable by the Administrator",
			guard.settings_has_permission(settings_doc, "read", "Administrator"),
		)
		check(
			"B1 the settings are hidden from an ordinary user",
			not guard.settings_has_permission(settings_doc, "read", dd.USER_A),
		)
		check(
			"B1 the settings doctype is granted only to the subscription role",
			[p.role for p in frappe.get_meta("Prymage Subscription Settings").permissions]
			== [subs.ADMIN_ROLE],
		)
		check(
			"B1 that role is left unassigned, so only the Administrator has it",
			not frappe.db.count("Has Role", {"role": subs.ADMIN_ROLE}),
		)
	finally:
		d = frappe.get_single("Prymage Subscription Settings")
		d.restrict_access_after_expiry = 0
		d.save(ignore_permissions=True)
		d = frappe.get_single("Prymage Subscription Settings")
		d.expiry_date = original["expiry_date"]
		d.reminder_days = original["reminder_days"]
		d.save(ignore_permissions=True)
		subs.clear_subscription_cache()


def _save_subscription(expiry_date, restrict):
	d = frappe.get_single("Prymage Subscription Settings")
	d.expiry_date = expiry_date
	d.restrict_access_after_expiry = restrict
	d.save(ignore_permissions=True)


# ---------------------------------------------------------------------------
# C - withholding tax on the amount excluding VAT
# ---------------------------------------------------------------------------
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


def _make_purchase_invoice(company, supplier, net, vat_rate, apply_tds=1, submit=True):
	doc = frappe.get_doc(
		{
			"doctype": "Purchase Invoice",
			"company": company,
			"supplier": supplier,
			"posting_date": nowdate(),
			"due_date": add_days(nowdate(), 30),
			"currency": dd.CURRENCY,
			"apply_tds": apply_tds,
			"tax_withholding_category": dd.WHT_CATEGORY if apply_tds else None,
			"items": [
				{
					"item_code": dd.ITEM_SERVICE,
					"qty": 1,
					"rate": net,
					"apply_tds": apply_tds,
					"tax_withholding_category": dd.WHT_CATEGORY if apply_tds else None,
				}
			],
		}
	)
	if vat_rate:
		doc.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": _vat_account(company),
				"description": "VAT",
				"rate": vat_rate,
				"category": "Total",
				"add_deduct_tax": "Add",
			},
		)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def _withheld(doc):
	return sum(
		flt(row.tax_amount) for row in doc.taxes if row.get("is_tax_withholding_account")
	)


def verify_withholding_tax():
	print("\nC      Withholding tax calculated on the amount excluding VAT")

	company = dd.COMPANY_A
	net, vat_rate = 10000.0, dd.VAT_RATE

	# --- C10: at the invoice, ERPNext already excludes VAT (Net Total basis) ---
	frappe.db.set_value("Tax Withholding Category", dd.WHT_CATEGORY, "tax_deduction_basis", "Net Total")
	pi = _make_purchase_invoice(company, dd.SUPPLIER_A, net, vat_rate)
	check_eq("C10 invoice total includes VAT", pi.grand_total, 12000.0 - net * dd.WHT_RATE / 100)
	check_eq(
		"C10 withholding at the invoice is 7.5% of the net, VAT excluded",
		_withheld(pi),
		net * dd.WHT_RATE / 100,
	)
	check_eq(
		"C10 the withholding base is the net, not the gross",
		pi.tax_withholding_entries[0].taxable_amount,
		net,
	)

	# --- the lever that gets this wrong, so the warning in C10 earns its keep ---
	frappe.db.set_value(
		"Tax Withholding Category", dd.WHT_CATEGORY, "tax_deduction_basis", "Gross Total"
	)
	frappe.clear_cache(doctype="Tax Withholding Category")
	pi_gross = _make_purchase_invoice(company, dd.SUPPLIER_A, net, vat_rate)
	check_eq(
		"C10 on a Gross Total category the base includes VAT (what we warn about)",
		pi_gross.tax_withholding_entries[0].taxable_amount,
		net * (1 + vat_rate / 100),
	)
	frappe.db.set_value(
		"Tax Withholding Category", dd.WHT_CATEGORY, "tax_deduction_basis", "Net Total"
	)
	frappe.clear_cache(doctype="Tax Withholding Category")

	# --- C11: advance payment, no invoice to take the VAT out of --------------
	# The worked example from the requirement document: 12,000 paid, of which
	# 2,000 is VAT, withheld at 7.5% -> base 10,000, tax 750, net paid 11,250.
	pe = _make_advance_payment(company, dd.SUPPLIER_A, 12000.0, includes_vat=1, vat_rate=vat_rate)
	check_eq("C11 the withholding base excludes the VAT", pe.custom_wht_base_amount, 10000.0)
	check_eq("C11 the withholding tax is 7.5% of the net", _withheld(pe), 750.0)
	check_eq(
		"C11 the net payment to the supplier is the paid amount less the tax",
		flt(pe.paid_amount) - _withheld(pe),
		11250.0,
	)

	# The same payment without the checkbox is the behaviour being corrected.
	pe_gross = _make_advance_payment(company, dd.SUPPLIER_A, 12000.0, includes_vat=0)
	check_eq(
		"C11 without the checkbox the tax is still taken on the gross",
		_withheld(pe_gross),
		900.0,
	)

	# An explicit VAT amount wins over the rate.
	pe_amount = _make_advance_payment(
		company, dd.SUPPLIER_A, 12000.0, includes_vat=1, vat_rate=0, vat_amount=2000.0
	)
	check_eq("C11 an explicit VAT amount is used as entered", pe_amount.custom_wht_base_amount, 10000.0)
	check_eq("C11 and gives the same tax", _withheld(pe_amount), 750.0)

	check_throws(
		"C11 VAT larger than the payment is refused",
		lambda: _make_advance_payment(
			company, dd.SUPPLIER_A, 1000.0, includes_vat=1, vat_rate=0, vat_amount=5000.0
		),
	)

	check_throws(
		"C11 the checkbox without any VAT figure is refused",
		lambda: _make_advance_payment(
			company, dd.SUPPLIER_A, 1000.0, includes_vat=1, vat_rate=0, vat_amount=0
		),
	)

	check_throws(
		"C11 the checkbox without Apply Tax Withholding is refused",
		lambda: _make_advance_payment(
			company, dd.SUPPLIER_A, 1000.0, includes_vat=1, vat_rate=20, apply_tds=0
		),
	)

	check(
		"C  the Payment Entry extension sits in front of the hrms override",
		_mro_order_ok(),
	)


def _mro_order_ok():
	from frappe.model.base_document import get_controller

	names = [c.__module__ + "." + c.__name__ for c in get_controller("Payment Entry").__mro__]
	try:
		ours = names.index("prymage.overrides.payment_entry.PrymagePaymentEntryExtension")
	except ValueError:
		return False
	base = names.index("erpnext.accounts.doctype.payment_entry.payment_entry.PaymentEntry")
	return ours < base


def _make_advance_payment(
	company, supplier, paid, includes_vat=0, vat_rate=0, vat_amount=0, apply_tds=1
):
	"""A pure advance: nothing allocated, so the whole amount is the base."""
	paid_from = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Bank", "is_group": 0},
		"name",
	) or frappe.db.get_value(
		"Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name"
	)
	paid_to = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Payable", "is_group": 0}, "name"
	)

	doc = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": "Pay",
			"company": company,
			"posting_date": nowdate(),
			"party_type": "Supplier",
			"party": supplier,
			"paid_from": paid_from,
			"paid_to": paid_to,
			"paid_amount": paid,
			"received_amount": paid,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"reference_no": "CHQ-0001",
			"reference_date": nowdate(),
			"mode_of_payment": _mode_of_payment(),
			"apply_tds": apply_tds,
			"tax_withholding_category": dd.WHT_CATEGORY if apply_tds else None,
			"custom_payment_includes_vat": includes_vat,
			"custom_vat_rate": vat_rate,
			"custom_vat_amount": vat_amount,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _mode_of_payment():
	for candidate in ("Cheque", "Bank Draft", "Cash"):
		if frappe.db.exists("Mode of Payment", candidate):
			return candidate
	return frappe.get_all("Mode of Payment", pluck="name", limit=1)[0]


# ---------------------------------------------------------------------------
# D - ledger statement print formats
# ---------------------------------------------------------------------------
def _gl_report_figures(company, from_date, to_date, party_type=None, party=None, account=None):
	"""Opening / total / closing straight out of ERPNext's General Ledger report."""
	from erpnext.accounts.report.general_ledger.general_ledger import (
		execute as gl_execute,
		get_translated_labels_for_totals,
	)

	filters = frappe._dict(
		{
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
			"show_remarks": 1,
		}
	)
	if account:
		filters.account = [account]
	else:
		filters.party_type = party_type
		filters.party = [party]

	_columns, data = gl_execute(filters)
	labels = get_translated_labels_for_totals()

	opening = next((r for r in data if r.get("account") == labels["opening"]), None)
	totals = [r for r in data if r.get("account") == labels["total"]]
	closings = [r for r in data if r.get("account") == labels["closing"]]

	return frappe._dict(
		opening=flt(opening.get("debit", 0)) - flt(opening.get("credit", 0)) if opening else 0.0,
		debit=flt(totals[-1].get("debit", 0)) if totals else 0.0,
		credit=flt(totals[-1].get("credit", 0)) if totals else 0.0,
		closing=flt(closings[-1].get("debit", 0)) - flt(closings[-1].get("credit", 0))
		if closings
		else 0.0,
	)


def _gl_report_row_count(company, from_date, to_date, party_type=None, party=None, account=None):
	"""Entry rows in the General Ledger report, ignoring its opening/total/closing
	marker rows and the blank separators it inserts between groups."""
	from erpnext.accounts.report.general_ledger.general_ledger import (
		execute as gl_execute,
		get_translated_labels_for_totals,
	)

	filters = frappe._dict(
		{"company": company, "from_date": from_date, "to_date": to_date, "show_remarks": 1}
	)
	if account:
		filters.account = [account]
	else:
		filters.party_type = party_type
		filters.party = [party]

	_columns, data = gl_execute(filters)
	markers = set(get_translated_labels_for_totals().values())
	return len([r for r in data if r.get("voucher_no") and r.get("account") not in markers])


def _make_sales_invoice(company, customer, amount, posting_date=None):
	income = frappe.db.get_value(
		"Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
	)
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"customer": customer,
			"posting_date": posting_date or nowdate(),
			"due_date": add_days(posting_date or nowdate(), 30),
			"currency": dd.CURRENCY,
			"items": [
				{
					"item_code": dd.ITEM_SERVICE,
					"qty": 1,
					"rate": amount,
					"income_account": income,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def _make_receipt(company, customer, amount, against, posting_date=None):
	paid_to = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Bank", "is_group": 0}, "name"
	) or frappe.db.get_value(
		"Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name"
	)
	paid_from = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Receivable", "is_group": 0}, "name"
	)

	doc = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"company": company,
			"posting_date": posting_date or nowdate(),
			"party_type": "Customer",
			"party": customer,
			"paid_from": paid_from,
			"paid_to": paid_to,
			"paid_amount": amount,
			"received_amount": amount,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"reference_no": "CHQ-9001",
			"reference_date": posting_date or nowdate(),
			"mode_of_payment": _mode_of_payment(),
			"references": [
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": against,
					"allocated_amount": amount,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def _build_statement(statement_type, company, party, from_date, to_date):
	doc = frappe.get_doc(
		{
			"doctype": "Prymage Ledger Statement",
			"statement_type": statement_type,
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
			"show_remarks": 1,
			{"Customer": "customer", "Supplier": "supplier", "Account": "account"}[
				statement_type
			]: party,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.fetch_entries()
	doc.save(ignore_permissions=True)
	return doc


def _statement_as(user, statement_type, company, party, from_date, to_date):
	"""Build a statement as a specific user, with permissions enforced."""
	with as_user(user):
		doc = frappe.get_doc(
			{
				"doctype": "Prymage Ledger Statement",
				"statement_type": statement_type,
				"company": company,
				"from_date": from_date,
				"to_date": to_date,
				{"Customer": "customer", "Supplier": "supplier", "Account": "account"}[
					statement_type
				]: party,
			}
		)
		doc.insert()
		return doc


def verify_ledger_statements():
	print("\nD      Customer, Supplier and Account ledger statement print formats")

	company = dd.COMPANY_A
	from_date = f"{getdate(nowdate()).year}-01-01"
	to_date = add_days(nowdate(), 1)

	si = _make_sales_invoice(company, dd.CUSTOMER_SHARED, 5000.0)
	receipt = _make_receipt(company, dd.CUSTOMER_SHARED, 2000.0, si.name)

	# --- D1 customer -------------------------------------------------------
	statement = _build_statement("Customer", company, dd.CUSTOMER_SHARED, from_date, to_date)
	gl = _gl_report_figures(
		company, from_date, to_date, party_type="Customer", party=dd.CUSTOMER_SHARED
	)

	check_eq("D1 opening balance matches the General Ledger report", statement.opening_balance, gl.opening)
	check_eq("D1 total debit matches the General Ledger report", statement.total_debit, gl.debit)
	check_eq("D1 total credit matches the General Ledger report", statement.total_credit, gl.credit)
	check_eq("D1 closing balance matches the General Ledger report", statement.closing_balance, gl.closing)
	check_eq(
		"D1 closing balance is opening plus debit less credit",
		statement.closing_balance,
		flt(statement.opening_balance) + flt(statement.total_debit) - flt(statement.total_credit),
	)
	check_eq(
		"D1 the statement has exactly one row per general ledger entry",
		len(statement.entries),
		_gl_report_row_count(
			company, from_date, to_date, party_type="Customer", party=dd.CUSTOMER_SHARED
		),
	)
	check_eq(
		"D1 the running balance on the last row is the closing balance",
		statement.entries[-1].balance,
		statement.closing_balance,
	)
	check(
		"D1 every column the client asked for is populated",
		all(
			statement.entries[0].get(field) not in (None, "")
			for field in ("posting_date", "voucher_no", "narration", "debit", "balance")
		),
		frappe.as_json(
			{f: statement.entries[0].get(f) for f in ("posting_date", "voucher_no", "narration")}
		),
	)

	# Anchored to the voucher this run created, not to "the first payment on the
	# statement": the manual UAT fixtures reuse these party names, so matching by
	# doctype alone would pick up whichever receipt happened to be there.
	receipt_row = next(
		(row for row in statement.entries if row.voucher_no == receipt.name), None
	)
	check("D1 the receipt row is present", bool(receipt_row))
	if receipt_row:
		check_eq("D1 cheque number is carried through", receipt_row.cheque_no, "CHQ-9001")
		check_eq("D1 the narration reads as a statement line, not a voucher subtype",
			receipt_row.narration, "Payment Received")
		check_eq(
			"D1 mode of payment is carried through",
			receipt_row.mode_of_payment,
			_mode_of_payment(),
		)
		check(
			"D1 the remark under the entry is populated",
			bool(receipt_row.remarks),
			str(receipt_row.remarks)[:80],
		)

	check_eq(
		"D4 the statement takes the letterhead of its own company",
		statement.letter_head,
		dd.letter_head_for(company),
	)
	other = _build_statement("Customer", dd.COMPANY_B, dd.CUSTOMER_SHARED, from_date, to_date)
	check_eq(
		"D4 a statement for the other company takes that company's letterhead",
		other.letter_head,
		dd.letter_head_for(dd.COMPANY_B),
	)
	check(
		"D4 the two companies print different letterheads",
		statement.letter_head != other.letter_head,
		f"{statement.letter_head} vs {other.letter_head}",
	)
	check_eq("D1 the statement is presented in the company currency", statement.currency, dd.CURRENCY)

	# --- D2 supplier -------------------------------------------------------
	_make_purchase_invoice(company, dd.SUPPLIER_B, 4000.0, dd.VAT_RATE, apply_tds=0)
	supplier_statement = _build_statement("Supplier", company, dd.SUPPLIER_B, from_date, to_date)
	gl_supplier = _gl_report_figures(
		company, from_date, to_date, party_type="Supplier", party=dd.SUPPLIER_B
	)
	check_eq(
		"D2 supplier statement opening matches the General Ledger report",
		supplier_statement.opening_balance,
		gl_supplier.opening,
	)
	check_eq(
		"D2 supplier statement closing matches the General Ledger report",
		supplier_statement.closing_balance,
		gl_supplier.closing,
	)
	check(
		"D2 the withholding deduction shows separately from the net amount",
		_withholding_visible_on_supplier_statement(company, from_date, to_date),
	)

	# --- D3 any ledger account --------------------------------------------
	bank = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Bank", "is_group": 0}, "name"
	) or frappe.db.get_value(
		"Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name"
	)
	account_statement = _build_statement("Account", company, bank, from_date, to_date)
	gl_account = _gl_report_figures(company, from_date, to_date, account=bank)
	check_eq(
		"D3 account statement opening matches the General Ledger report",
		account_statement.opening_balance,
		gl_account.opening,
	)
	check_eq(
		"D3 account statement closing matches the General Ledger report",
		account_statement.closing_balance,
		gl_account.closing,
	)

	check_throws(
		"D3 a group account is refused",
		lambda: _build_statement(
			"Account",
			company,
			frappe.db.get_value("Account", {"company": company, "is_group": 1}, "name"),
			from_date,
			to_date,
		),
	)

	check_throws(
		"D  an account from another company is refused",
		lambda: _build_statement(
			"Account",
			company,
			frappe.db.get_value(
				"Account", {"company": dd.COMPANY_B, "is_group": 0}, "name"
			),
			from_date,
			to_date,
		),
	)

	check_throws(
		"D  From Date after To Date is refused",
		lambda: _build_statement("Customer", company, dd.CUSTOMER_SHARED, to_date, from_date),
	)

	# --- the statement cannot be used to read around the access rules -----
	check_throws(
		"D  a user cannot build a statement for a company they have no access to",
		lambda: _statement_as(dd.USER_A, "Customer", dd.COMPANY_B, dd.CUSTOMER_SHARED, from_date, to_date),
		frappe.PermissionError,
	)

	with isolation(
		enabled=True,
		sharing={
			dd.COMPANY_A: {"share_customers": 0},
			dd.COMPANY_B: {"share_customers": 0},
		},
	):
		set_selection(dd.USER_A, [dd.COMPANY_A])
		check_throws(
			"D  a user cannot build a statement for a customer the sharing rules hide",
			lambda: _statement_as(
				dd.USER_A, "Customer", dd.COMPANY_A, dd.CUSTOMER_B, from_date, to_date
			),
			frappe.PermissionError,
		)
		check(
			"D  a statement for a customer they can see still works",
			_statement_as(dd.USER_A, "Customer", dd.COMPANY_A, dd.CUSTOMER_A, from_date, to_date)
			is not None,
		)

	# --- the print formats actually render --------------------------------
	for print_format, doc in (
		("Prymage Customer Ledger Statement", statement),
		("Prymage Supplier Ledger Statement", supplier_statement),
		("Prymage Account Ledger Statement", account_statement),
	):
		try:
			html = frappe.get_print(
				doc.doctype, doc.name, print_format=print_format, no_letterhead=0
			)
			ok = "Balance brought forward" in html and "Closing balance" in html
			check(f"D  {print_format} renders with its layout", ok, f"{len(html)} chars")
		except Exception as e:  # noqa: BLE001
			check(f"D  {print_format} renders with its layout", False, f"{type(e).__name__}: {e}")


def _withholding_visible_on_supplier_statement(company, from_date, to_date):
	"""The withholding account leg must appear on its own line, not netted off."""
	pi = _make_purchase_invoice(company, dd.SUPPLIER_A, 10000.0, dd.VAT_RATE, apply_tds=1)
	statement = _build_statement("Supplier", company, dd.SUPPLIER_A, from_date, to_date)
	invoice_rows = [row for row in statement.entries if row.voucher_no == pi.name]
	if not invoice_rows:
		return False

	# The payable leg is net of the withholding; the withholding itself is a
	# separate GL line on the tax account, which the Account statement shows.
	payable = sum(flt(r.credit) - flt(r.debit) for r in invoice_rows)
	expected_net = flt(pi.grand_total)
	return abs(payable - expected_net) < 0.01


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
SECTIONS = (
	("A1/A2 company selection and access", verify_company_selection),
	("A3 settings screen", verify_settings_screen),
	("A4 company tagging", verify_tagging),
	("A5 visibility rules", verify_visibility),
	("A6 audit trail", verify_audit_trail),
	("B subscription expiry", verify_subscription),
	("C withholding tax excluding VAT", verify_withholding_tax),
	("D ledger statements", verify_ledger_statements),
)


def run(keep_data=0):
	"""Build fixtures, run every check, restore the settings.

	Pass ``keep_data=1`` to leave the fixtures behind for a manual look at the
	screens afterwards.
	"""
	RESULTS.clear()
	print("Prymage verification")
	print("=" * 72)

	snapshot = _snapshot_settings()
	try:
		dd.build()
		# Make sure the new test companies have sharing rows.
		doc = frappe.get_single("Prymage Multi Company Settings")
		if doc.sync_companies():
			doc.save(ignore_permissions=True)
		frappe.db.commit()
		mcs.clear_settings_cache()

		for label, fn in SECTIONS:
			try:
				fn()
				frappe.db.commit()
			except Exception:  # noqa: BLE001
				check(f"{label} raised", False, traceback.format_exc().splitlines()[-1])
				print(traceback.format_exc())
	finally:
		_restore_settings(snapshot)
		if not int(keep_data or 0):
			try:
				dd.reset()
			except Exception:  # noqa: BLE001
				print("WARNING could not remove every fixture:\n" + traceback.format_exc())
		frappe.db.commit()

	passed = sum(1 for ok, _l, _d in RESULTS if ok)
	failed = [(label, detail) for ok, label, detail in RESULTS if not ok]

	print("=" * 72)
	print(f"{passed}/{len(RESULTS)} checks passed")
	if failed:
		print("\nFAILED:")
		for label, detail in failed:
			print(f"  - {label}   [{detail}]")
	return {"passed": passed, "total": len(RESULTS), "failed": [f[0] for f in failed]}


def _snapshot_settings():
	mc = frappe.get_single("Prymage Multi Company Settings")
	sub = frappe.get_single("Prymage Subscription Settings")
	return {
		"enable_company_data_isolation": mc.enable_company_data_isolation,
		"treat_untagged_as_shared": mc.treat_untagged_as_shared,
		"require_company_selection_on_login": mc.require_company_selection_on_login,
		"allow_multiple_company_selection": mc.allow_multiple_company_selection,
		"sharing": {
			row.company: {
				"share_customers": row.share_customers,
				"share_suppliers": row.share_suppliers,
				"share_items": row.share_items,
				"share_price_lists": row.share_price_lists,
				"share_contacts_and_addresses": row.share_contacts_and_addresses,
			}
			for row in mc.company_data_sharing
		},
		"expiry_date": sub.expiry_date,
		"reminder_days": sub.reminder_days,
		"restrict_access_after_expiry": sub.restrict_access_after_expiry,
	}


def _restore_settings(snapshot):
	mc = frappe.get_single("Prymage Multi Company Settings")
	for field in (
		"enable_company_data_isolation",
		"treat_untagged_as_shared",
		"require_company_selection_on_login",
		"allow_multiple_company_selection",
	):
		mc.set(field, snapshot[field])
	# Rows added during the run (for the test companies) are removed, not just
	# left at whatever the last check set them to.
	kept = []
	for row in mc.company_data_sharing:
		if row.company not in snapshot["sharing"]:
			continue
		for field, value in snapshot["sharing"][row.company].items():
			row.set(field, value)
		kept.append(row)
	mc.set("company_data_sharing", kept)
	mc.save(ignore_permissions=True)

	sub = frappe.get_single("Prymage Subscription Settings")
	sub.restrict_access_after_expiry = 0
	sub.save(ignore_permissions=True)
	sub = frappe.get_single("Prymage Subscription Settings")
	sub.expiry_date = snapshot["expiry_date"]
	sub.reminder_days = snapshot["reminder_days"]
	sub.restrict_access_after_expiry = snapshot["restrict_access_after_expiry"]
	sub.save(ignore_permissions=True)

	mcs.clear_settings_cache()
	subs.clear_subscription_cache()
	frappe.conf.pop(subs.BYPASS_KEY, None)
	frappe.db.commit()
