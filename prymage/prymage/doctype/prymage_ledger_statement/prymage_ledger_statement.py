# Copyright (c) 2026, Riddhi and contributors
# For license information, please see license.txt
"""Customer / Supplier / Account ledger statement (requirement D).

The statement is built straight from GL Entry using the same rules the standard
General Ledger report uses, so the printed statement and the General Ledger
report always agree:

* no lower date bound when a party or an account is filtered, so prior entries
  can accumulate into the opening balance;
* an entry lands in the opening balance when ``posting_date < from_date`` **or**
  it is flagged ``is_opening = Yes`` (unless Accounts Settings
  ``ignore_is_opening_check_for_reporting`` is on);
* cancelled entries are excluded;
* only entries with no finance book are included, which is what the General
  Ledger report does unless a finance book is explicitly chosen.

``prymage/tests/verify.py`` asserts the figures against
``erpnext.accounts.report.general_ledger.execute`` for every statement it builds.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, flt, getdate

#: Where the cheque number, mode of payment and narration live on each voucher.
#: Resolved against the doctype meta at run time, so a field that does not exist
#: on a given ERPNext version degrades to blank instead of raising.
VOUCHER_FIELD_MAP = {
	"Journal Entry": {
		"cheque_no": "cheque_no",
		"mode_of_payment": "mode_of_payment",
		"narration": "user_remark",
	},
	"Payment Entry": {
		"cheque_no": "reference_no",
		"mode_of_payment": "mode_of_payment",
		"payment_type": "payment_type",
	},
	"Purchase Invoice": {
		"mode_of_payment": "mode_of_payment",
	},
}


class PrymageLedgerStatement(Document):
	def validate(self):
		self.validate_period()
		self.validate_party()
		self.validate_party_is_readable()
		self.set_party_display_name()
		self.set_letter_head()

	def validate_period(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date must be on or before To Date."))

	def validate_party(self):
		"""Exactly one party field must be set, and it must match the type."""
		expected = {"Customer": "customer", "Supplier": "supplier", "Account": "account"}[
			self.statement_type
		]
		for field in ("customer", "supplier", "account"):
			if field != expected:
				self.set(field, None)

		if not self.get(expected):
			frappe.throw(_("{0} is required for a {1} statement.").format(_(expected.title()), _(self.statement_type)))

		if self.statement_type == "Account":
			account_company, is_group = frappe.db.get_value(
				"Account", self.account, ["company", "is_group"]
			)
			if account_company != self.company:
				frappe.throw(
					_("Account {0} belongs to {1}, not to {2}.").format(
						frappe.bold(self.account), frappe.bold(account_company), frappe.bold(self.company)
					)
				)
			if is_group:
				frappe.throw(
					_("{0} is a group account. Pick the ledger account the entries are posted to.").format(
						frappe.bold(self.account)
					)
				)

	def validate_party_is_readable(self):
		"""Do not let a statement read around the multi-company visibility rules.

		The company field is a plain Link, so User Permissions on Company
		already stop a user picking a company they have no access to. The party
		is different: the Prymage sharing rules hide a customer from the list
		and the dropdown, but Frappe's link validation only checks that the
		record exists. Without this a user could type the name of a customer
		their company is not allowed to see and print its ledger.
		"""
		party_doctype = self.statement_type
		if party_doctype == "Account":
			return

		party = self.get(party_doctype.lower())
		if not party:
			return

		if not frappe.has_permission(party_doctype, "read", doc=party):
			frappe.throw(
				_("You are not permitted to read {0} {1}.").format(_(party_doctype), frappe.bold(party)),
				frappe.PermissionError,
			)

	def set_party_display_name(self):
		if self.statement_type == "Customer":
			self.party_display_name = (
				frappe.db.get_value("Customer", self.customer, "customer_name") or self.customer
			)
			self.tax_id = frappe.db.get_value("Customer", self.customer, "tax_id") or ""
		elif self.statement_type == "Supplier":
			self.party_display_name = (
				frappe.db.get_value("Supplier", self.supplier, "supplier_name") or self.supplier
			)
			self.tax_id = frappe.db.get_value("Supplier", self.supplier, "tax_id") or ""
		else:
			self.party_display_name = self.account
			self.tax_id = ""

	def set_letter_head(self):
		"""D4 - each statement prints with its own company's letterhead and logo.

		The company's letterhead has to win over the site-wide default, because
		a group with several companies must print each one correctly. Frappe
		seeds a ``letter_head`` field on a new document from the global default
		(``frappe.defaults``), so an empty check on its own is not enough.

		A letterhead the user picked deliberately is still respected: only an
		empty value, the global default, or the previous company's letterhead is
		replaced.
		"""
		if not self.company:
			return

		company_letter_head = frappe.db.get_value("Company", self.company, "default_letter_head")
		if not company_letter_head:
			return

		if not self.letter_head:
			self.letter_head = company_letter_head
			return

		if self.letter_head == company_letter_head:
			return

		replaceable = {frappe.defaults.get_global_default("letter_head")}
		previous = self.get_doc_before_save()
		if previous and previous.company != self.company:
			replaceable.add(
				frappe.db.get_value("Company", previous.company, "default_letter_head")
			)

		if self.letter_head in replaceable:
			self.letter_head = company_letter_head

	# ------------------------------------------------------------------
	# Data
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def fetch_entries(self):
		"""Rebuild the statement lines from the general ledger."""
		self.validate_period()
		self.validate_party()
		self.set_party_display_name()
		self.set_letter_head()

		result = build_statement(
			statement_type=self.statement_type,
			company=self.company,
			party=self.customer or self.supplier or self.account,
			from_date=self.from_date,
			to_date=self.to_date,
		)

		self.currency = result["currency"]
		self.opening_balance = result["opening_balance"]
		self.total_debit = result["total_debit"]
		self.total_credit = result["total_credit"]
		self.closing_balance = result["closing_balance"]
		self.generated_on = frappe.utils.now()

		self.set("entries", [])
		for row in result["entries"]:
			self.append("entries", row)

		return {"rows": len(result["entries"])}


def build_statement(statement_type: str, company: str, party: str, from_date, to_date) -> dict:
	"""Pure data function: no document required, so tests can call it directly."""
	from_date, to_date = getdate(from_date), getdate(to_date)

	ignore_is_opening = bool(
		frappe.get_single_value("Accounts Settings", "ignore_is_opening_check_for_reporting")
	)

	gl_entries = _get_gl_entries(statement_type, company, party, to_date, ignore_is_opening)

	currency, use_account_currency = _resolve_currency(gl_entries, statement_type, company, party)

	opening = 0.0
	period = []
	for gle in gl_entries:
		debit, credit = _amounts(gle, use_account_currency)
		is_opening_entry = cstr(gle.is_opening) == "Yes" and not ignore_is_opening

		if gle.posting_date < from_date or is_opening_entry:
			opening += debit - credit
		elif gle.posting_date <= to_date:
			period.append((gle, debit, credit))

	precision = frappe.get_precision("Prymage Ledger Statement Entry", "debit") or 2
	opening = flt(opening, precision)

	voucher_details = _get_voucher_details(period)

	entries = []
	running = opening
	total_debit = total_credit = 0.0
	for gle, debit, credit in period:
		running = flt(running + debit - credit, precision)
		total_debit += debit
		total_credit += credit
		extra = voucher_details.get((gle.voucher_type, gle.voucher_no), {})
		entries.append(
			{
				"posting_date": gle.posting_date,
				"voucher_type": gle.voucher_type,
				"voucher_no": gle.voucher_no,
				"narration": extra.get("narration") or _(gle.voucher_subtype or gle.voucher_type or ""),
				"cheque_no": extra.get("cheque_no") or "",
				"mode_of_payment": extra.get("mode_of_payment") or "",
				"against_account": gle.against or "",
				"debit": flt(debit, precision),
				"credit": flt(credit, precision),
				"balance": running,
				"remarks": gle.remarks or "",
				"is_opening_entry": 0,
			}
		)

	return {
		"currency": currency,
		"opening_balance": opening,
		"total_debit": flt(total_debit, precision),
		"total_credit": flt(total_credit, precision),
		"closing_balance": flt(opening + total_debit - total_credit, precision),
		"entries": entries,
	}


def _get_gl_entries(statement_type, company, party, to_date, ignore_is_opening):
	conditions = ["company = %(company)s", "is_cancelled = 0"]
	values = {"company": company, "to_date": to_date}

	if statement_type == "Account":
		conditions.append("account = %(account)s")
		values["account"] = party
	else:
		conditions.append("party_type = %(party_type)s")
		conditions.append("party = %(party)s")
		values["party_type"] = statement_type
		values["party"] = party

	# Mirrors the General Ledger report: with a party/account filter there is no
	# lower date bound, so everything before From Date rolls into the opening.
	if ignore_is_opening:
		conditions.append("posting_date <= %(to_date)s")
	else:
		conditions.append("(posting_date <= %(to_date)s or is_opening = 'Yes')")

	# The General Ledger report excludes finance-book entries unless a finance
	# book is chosen. Statements follow the same default so the two agree.
	conditions.append("(finance_book is null or finance_book = '')")

	return frappe.db.sql(
		"""
		select
			posting_date, account, party_type, party, voucher_type, voucher_subtype,
			voucher_no, against, against_voucher_type, against_voucher, remarks,
			is_opening, account_currency, debit, credit,
			debit_in_account_currency, credit_in_account_currency
		from `tabGL Entry`
		where {conditions}
		order by posting_date, creation
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)


def _resolve_currency(gl_entries, statement_type, company, party):
	"""Present in the account currency when every entry shares one, else company.

	Amounts are always *read* from the stored column for that currency - never
	divided by an exchange rate - so the statement matches the voucher.
	"""
	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	currencies = {gle.account_currency for gle in gl_entries if gle.account_currency}

	if len(currencies) == 1:
		only = currencies.pop()
		if only != company_currency:
			return only, True
		return company_currency, False

	if not currencies and statement_type == "Account":
		account_currency = frappe.db.get_value("Account", party, "account_currency")
		if account_currency and account_currency != company_currency:
			return account_currency, True

	return company_currency, False


def _amounts(gle, use_account_currency: bool):
	if use_account_currency:
		return flt(gle.debit_in_account_currency), flt(gle.credit_in_account_currency)
	return flt(gle.debit), flt(gle.credit)


def _get_voucher_details(period) -> dict:
	"""One query per voucher type for the cheque no / mode of payment / narration."""
	details: dict[tuple, dict] = {}
	names_by_type: dict[str, set] = {}
	for gle, _debit, _credit in period:
		names_by_type.setdefault(gle.voucher_type, set()).add(gle.voucher_no)

	for voucher_type, names in names_by_type.items():
		mapping = VOUCHER_FIELD_MAP.get(voucher_type)
		if not mapping or not names:
			continue

		if not frappe.db.exists("DocType", voucher_type):
			continue

		meta = frappe.get_meta(voucher_type)
		available = {key: field for key, field in mapping.items() if meta.get_field(field)}
		if not available:
			continue

		rows = frappe.get_all(
			voucher_type,
			filters={"name": ("in", list(names))},
			fields=["name", *available.values()],
			ignore_permissions=True,
		)
		for row in rows:
			detail = {key: row.get(field) for key, field in available.items()}
			_derive_narration(voucher_type, detail)
			details[(voucher_type, row.name)] = detail

	return details


def _derive_narration(voucher_type: str, detail: dict):
	"""Give a Payment Entry a narration that reads on a printed statement.

	Falling back to ERPNext's voucher subtype would print the bare word
	"Receive" or "Pay" in the Narration column, which means nothing to the
	customer holding the statement.
	"""
	if voucher_type != "Payment Entry":
		return

	payment_type = detail.pop("payment_type", None)
	if detail.get("narration"):
		return

	detail["narration"] = {
		"Receive": _("Payment Received"),
		"Pay": _("Payment Made"),
		"Internal Transfer": _("Internal Transfer"),
	}.get(payment_type, _("Payment Entry"))
