"""Company selection state for the logged-in session (requirement A1).

The selection is kept in Frappe's own user defaults, which is what every
``company`` field on every form and report already reads
(``frappe.defaults.get_user_default("company")``). Two keys are written:

``company``
        the primary company, so new documents default to it;
``prymage_selected_companies``
        the full selection as a comma separated string, used by the visibility
        rules. Kept separate from ``company`` so "nothing selected yet" can be
        told apart from "a global default exists".

Both are cleared on logout, so the next login goes through the selection screen
again.
"""

import frappe

from prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings import (
	get_settings,
)

SELECTION_KEY = "prymage_selected_companies"
COMPANY_KEY = "company"
SELECTION_PAGE = "app/company-selection"


# ---------------------------------------------------------------------------
# Reading the selection
# ---------------------------------------------------------------------------
def get_selected_companies(user: str | None = None) -> list[str]:
	raw = frappe.defaults.get_user_default(SELECTION_KEY, user or frappe.session.user)
	if not raw:
		return []
	return [c for c in (part.strip() for part in str(raw).split(",")) if c]


def get_primary_company(user: str | None = None) -> str | None:
	selected = get_selected_companies(user)
	return selected[0] if selected else None


def get_allowed_companies(user: str | None = None) -> list[str]:
	"""Companies the user may pick from.

	A User Permission on Company is what restricts this (requirement A2). With
	no User Permission the user may pick any company, which is standard Frappe
	behaviour and keeps single-company sites untouched.

	Memoised per request: the visibility rules call this on every list query,
	every link-field lookup and every report, and the company list cannot change
	inside one request.
	"""
	user = user or frappe.session.user

	cache = getattr(frappe.local, "prymage_allowed_companies", None)
	if cache is None:
		cache = frappe.local.prymage_allowed_companies = {}
	if user in cache:
		return cache[user]

	result = _compute_allowed_companies(user)
	cache[user] = result
	return result


def _compute_allowed_companies(user: str) -> list[str]:
	all_companies = frappe.get_all("Company", pluck="name", order_by="name")

	if user == "Administrator":
		return all_companies

	permitted = frappe.permissions.get_user_permissions(user).get("Company") or []
	allowed = [row.get("doc") for row in permitted if row.get("doc")]
	if not allowed:
		return all_companies

	# Preserve the alphabetical order and drop permissions for deleted companies.
	return [company for company in all_companies if company in allowed]


# ---------------------------------------------------------------------------
# Writing the selection
# ---------------------------------------------------------------------------
def set_selected_companies(companies: list[str], user: str | None = None):
	user = user or frappe.session.user
	companies = [c for c in companies if c]
	if not companies:
		clear_selection(user)
		return []

	frappe.defaults.set_user_default(SELECTION_KEY, ",".join(companies), user)
	frappe.defaults.set_user_default(COMPANY_KEY, companies[0], user)
	clear_request_cache()
	return companies


def clear_selection(user: str | None = None):
	user = user or frappe.session.user
	frappe.defaults.clear_user_default(SELECTION_KEY, user)
	frappe.defaults.clear_user_default(COMPANY_KEY, user)
	clear_request_cache()


def clear_request_cache():
	frappe.local.prymage_allowed_companies = {}


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
def on_session_creation():
	"""Send the user to the company selection screen after login.

	``frappe.local.flags.home_page`` is read first by
	``frappe.website.utils.get_home_page()``, which runs later in the same
	request (``LoginManager.set_user_info``) to fill ``response.home_page``.
	The login page then redirects there. Setting ``response["home_page"]``
	directly here would not survive, because ``set_user_info`` overwrites it.
	"""
	if frappe.get_cached_value("User", frappe.session.user, "user_type") == "Website User":
		return

	settings = get_settings()
	if not settings.require_selection:
		# Feature off: leave the user's company default exactly as it was, so a
		# site that does not use company selection behaves like plain ERPNext.
		return

	# Every login starts without a selection, otherwise yesterday's choice
	# would silently carry over and a user could post into the wrong company.
	clear_selection()

	allowed = get_allowed_companies()
	if not allowed:
		return

	if len(allowed) == 1 and settings.skip_single:
		set_selected_companies(allowed)
		return

	frappe.local.flags.home_page = SELECTION_PAGE


def on_logout():
	clear_selection()


# ---------------------------------------------------------------------------
# Whitelisted API used by the selection screen
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_company_options():
	settings = get_settings()
	allowed = get_allowed_companies()
	companies = (
		frappe.get_all(
			"Company",
			filters={"name": ("in", allowed)},
			fields=["name", "company_name", "abbr", "default_currency", "country", "company_logo"],
			order_by="name",
		)
		if allowed
		else []
	)
	return {
		"companies": companies,
		"selected": get_selected_companies(),
		"allow_multiple": settings.allow_multiple,
		"isolation_enabled": settings.enabled,
		"full_name": frappe.utils.get_fullname(frappe.session.user),
	}


@frappe.whitelist()
def select_companies(companies):
	"""Store the user's choice. Refuses anything they are not allowed to pick."""
	if isinstance(companies, str):
		companies = frappe.parse_json(companies)
	if isinstance(companies, str):
		companies = [companies]

	companies = [c for c in (companies or []) if c]
	if not companies:
		frappe.throw(frappe._("Select at least one company."))

	allowed = set(get_allowed_companies())
	invalid = [c for c in companies if c not in allowed]
	if invalid:
		frappe.throw(
			frappe._("You do not have access to {0}.").format(frappe.bold(", ".join(invalid))),
			frappe.PermissionError,
		)

	settings = get_settings()
	if not settings.allow_multiple and len(companies) > 1:
		frappe.throw(frappe._("Only one company can be selected at a time."))

	set_selected_companies(companies)
	# The home page is cached per user and may point at the selection screen.
	frappe.cache.hdel("home_page", frappe.session.user)
	return {"selected": companies}
