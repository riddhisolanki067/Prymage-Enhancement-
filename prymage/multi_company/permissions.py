"""Visibility rules for shared master data (requirement A5).

One rule, applied in three places so a user can never reach a record the rules
hide:

* ``permission_query_conditions`` - list views, Report Builder, the link-field
  dropdowns and every ``frappe.get_list`` / REST call. ERPNext's own raw-SQL
  link searches (``customer_query``, ``supplier_query``, ``item_query``) route
  through ``frappe.desk.reportview.get_match_cond``, which calls the same hook,
  so those are covered too.
* ``has_permission`` - opening a document directly by name or URL.
* the company tag written on insert (``tagging.py``) - what the rules read.

The rule, for the set of companies ``S`` the user currently has selected:

* a record with **no** company tag is always visible, to every company - this
  is what keeps the feature from hiding anything on the day it is switched on,
  before a client has tagged their existing data;
* a record owned by a company in ``S`` is always visible;
* a record owned by another company is visible only when **all three** hold:
  that owner shares the entity, at least one company in ``S`` accepts shared
  data, **and** the user holds a standing User Permission for that owner
  company. Sharing extends what a company with access can see; it is never
  a substitute for being given access in the first place.

Known limit, stated plainly: Query Reports that build their own SQL are not
interceptable by any Frappe hook, so a master-data Query Report can still show
rows these rules would hide. Transactional reports are unaffected because they
are already filtered by company.
"""

import frappe

from prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings import (
	SHARE_FIELD_BY_DOCTYPE,
	get_settings,
	shares,
)
from prymage.multi_company.session import get_allowed_companies, get_selected_companies

OWNER_FIELD = "custom_owner_company"


def get_effective_companies(user: str | None = None) -> list[str]:
	"""The companies the user is working in right now.

	Falls back to every company they are permitted, so a session that never went
	through the selection screen (an API token, a background job, login while
	the selection screen is switched off) is not locked out of everything.
	"""
	selected = get_selected_companies(user)
	return selected or get_allowed_companies(user)


def get_visible_companies(doctype: str, user: str | None = None) -> list[str]:
	"""Companies whose records of `doctype` this user may see.

	Sharing alone is not enough to pull another company's records into view -
	the user must also hold a standing User Permission for that company
	(``get_allowed_companies``, requirement A2). Without this, ticking two
	companies to share with each other would leak their master data to a user
	who was deliberately never given access to one of them, with no way to
	stop it short of turning sharing off for everyone. ``get_allowed_companies``
	already returns every company when the site has no User Permissions set up
	at all, so a site that never configured A2 is unaffected.
	"""
	settings = get_settings()
	own = get_effective_companies(user)
	allowed = set(get_allowed_companies(user))

	accepts_shared = any(shares(settings, company, doctype) for company in own)
	if not accepts_shared:
		return sorted(set(own))

	sharing = [
		company
		for company in settings.sharing
		if shares(settings, company, doctype) and company in allowed
	]
	return sorted(set(own) | set(sharing))


def _is_exempt(user: str) -> bool:
	# The Administrator must always be able to see and fix every record,
	# otherwise a misconfiguration is unrecoverable from the desk.
	return user == "Administrator"


def build_conditions(doctype: str, user: str | None = None) -> str:
	"""SQL fragment for `permission_query_conditions`.

	Columns are fully qualified: the same string is spliced into ERPNext's raw
	SQL link queries, where bare column names would be ambiguous.
	"""
	user = user or frappe.session.user

	if doctype not in SHARE_FIELD_BY_DOCTYPE:
		return ""

	settings = get_settings()
	if not settings.enabled or _is_exempt(user):
		return ""

	table = f"`tab{doctype}`"
	column = f"{table}.`{OWNER_FIELD}`"

	# An untagged record is always shared - unconditionally, there is no
	# setting that can turn this off. See the module docstring.
	clauses = [f"({column} is null or {column} = '')"]

	visible = get_visible_companies(doctype, user)
	if visible:
		values = ", ".join(frappe.db.escape(company) for company in visible)
		clauses.append(f"{column} in ({values})")

	return "(" + " or ".join(clauses) + ")"


def has_permission(doc, ptype=None, user=None, debug=False):
	"""Mirror of `build_conditions` for a single document."""
	user = user or frappe.session.user

	doctype = getattr(doc, "doctype", None)
	if doctype not in SHARE_FIELD_BY_DOCTYPE:
		return True

	settings = get_settings()
	if not settings.enabled or _is_exempt(user):
		return True

	owner_company = doc.get(OWNER_FIELD)
	if not owner_company:
		# Unconditional - see build_conditions() and the module docstring.
		return True

	return owner_company in get_visible_companies(doctype, user)


# ---------------------------------------------------------------------------
# One thin wrapper per doctype: the hook passes `user` positionally and the
# doctype as a keyword, so a single shared function would lose the doctype on
# the `has_permission` side. Keeping them explicit also makes hooks.py readable.
# ---------------------------------------------------------------------------
def customer_query_conditions(user=None, doctype="Customer"):
	return build_conditions("Customer", user)


def supplier_query_conditions(user=None, doctype="Supplier"):
	return build_conditions("Supplier", user)


def item_query_conditions(user=None, doctype="Item"):
	return build_conditions("Item", user)


def price_list_query_conditions(user=None, doctype="Price List"):
	return build_conditions("Price List", user)


def contact_query_conditions(user=None, doctype="Contact"):
	return build_conditions("Contact", user)


def address_query_conditions(user=None, doctype="Address"):
	return build_conditions("Address", user)
