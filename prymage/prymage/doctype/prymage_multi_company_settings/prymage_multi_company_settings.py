# Copyright (c) 2026, Riddhi and contributors
# For license information, please see license.txt
"""Which doctypes participate in company data isolation is itself data now,
not code. Add a row to ``shareable_doctypes`` and save this settings doc:
the "Owned by Company" field is created on that doctype automatically, and
every company gets a sharing row for it - no developer, no new hooks.py
entry, no migrate. See ``prymage/custom_fields.py`` (reads this table
instead of a hardcoded dict) and ``prymage/multi_company/permissions.py`` /
``tagging.py`` (checked against the same table via ``is_shareable``) for the
other end of this.
"""

import frappe
from frappe import _
from frappe.model.document import Document

CACHE_KEY = "prymage_multi_company_settings"

#: Used only to seed the registry the first time this app runs on a site
#: (``prymage/install.py``). Nothing else reads this - the live registry
#: (``shareable_doctypes``) is the only source of truth from here on. Where
#: each one's "Owned by Company" field lands is computed live, not stored -
#: see ``_first_tab_anchor()`` in ``prymage/custom_fields.py``.
DEFAULT_SHAREABLE_DOCTYPES = ["Customer", "Supplier", "Item", "Price List", "Contact", "Address"]

#: Old fixed Check column -> doctype(s) it used to gate. Only read by the
#: one-time legacy migration in install.py.
LEGACY_SHARE_FIELD_TO_DOCTYPES = {
	"share_customers": ["Customer"],
	"share_suppliers": ["Supplier"],
	"share_items": ["Item"],
	"share_price_lists": ["Price List"],
	"share_contacts_and_addresses": ["Contact", "Address"],
}


class PrymageMultiCompanySettings(Document):
	def validate(self):
		self.validate_unique_shareable_doctypes()
		self.sync_matrix()
		self.validate_unique_company_doctype_pairs()

	def validate_unique_company_doctype_pairs(self):
		"""Defensive only - sync_matrix() runs just above and only ever adds
		a (company, document_type) pair once, so this should never actually
		fire from normal use; it guards against a row added by hand outside
		that flow (an import, a manual API call).
		"""
		seen = set()
		for row in self.company_doctype_sharing:
			pair = (row.company, row.document_type)
			if pair in seen:
				frappe.throw(
					_("{0} / {1} is listed more than once in Company / DocType Sharing.").format(
						frappe.bold(row.company), frappe.bold(row.document_type)
					)
				)
			seen.add(pair)

	def validate_unique_shareable_doctypes(self):
		seen = set()
		for row in self.shareable_doctypes:
			if row.document_type in seen:
				frappe.throw(
					_("{0} is listed more than once in Shareable DocTypes.").format(
						frappe.bold(row.document_type)
					)
				)
			seen.add(row.document_type)

	def on_update(self):
		clear_settings_cache()
		# After, not inside validate(): this creates Custom Field documents
		# for whatever is now in shareable_doctypes, which is its own
		# document family - doing that once this document's own write has
		# safely landed avoids nesting another doctype's insert/update
		# inside this one's own validation.
		from prymage.custom_fields import setup_custom_fields

		setup_custom_fields()

	def sync_matrix(self):
		"""Reconcile company_doctype_sharing with the current Company list
		*and* the current shareable_doctypes list - both dimensions of the
		matrix, kept here (inside validate(), not a separate whitelisted
		step) so adding one new DocType row and saving immediately produces
		a sharing row for it against every company, in the same save - no
		extra click, no reload needed to see it took effect.

		New companies and newly-registered doctypes default to *sharing* -
		consistent with the original single-dimension version of this
		method: adding either dimension can never silently hide master data
		that used to be visible.
		"""
		companies = frappe.get_all("Company", pluck="name", order_by="name")
		doctypes = [row.document_type for row in self.shareable_doctypes if row.document_type]
		valid_pairs = {(c, d) for c in companies for d in doctypes}

		existing_pairs = {(row.company, row.document_type) for row in self.company_doctype_sharing}
		changed = False

		stale = [row for row in self.company_doctype_sharing if (row.company, row.document_type) not in valid_pairs]
		if stale:
			self.set(
				"company_doctype_sharing",
				[row for row in self.company_doctype_sharing if (row.company, row.document_type) in valid_pairs],
			)
			changed = True

		for company in companies:
			for doctype in doctypes:
				if (company, doctype) not in existing_pairs:
					self.append(
						"company_doctype_sharing",
						{"company": company, "document_type": doctype, "is_shared": 1},
					)
					changed = True

		return changed


@frappe.whitelist()
def sync_matrix():
	"""Manual trigger for the same reconciliation validate() already does on
	every save - useful when a Company was added elsewhere and nothing else
	on this form changed, so there would otherwise be no save to trigger it.
	"""
	frappe.only_for("System Manager")
	doc = frappe.get_single("Prymage Multi Company Settings")
	if doc.sync_matrix():
		doc.save()
		return {"added": True}
	return {"added": False}


def clear_settings_cache():
	frappe.cache.delete_value(CACHE_KEY)


def get_settings() -> frappe._dict:
	"""Cached, read-only view of the settings.

	Read on nearly every list query, so it must not hit the database each time.
	"""

	def _load():
		empty = {
			"enabled": False,
			"require_selection": False,
			"skip_single": False,
			"allow_multiple": False,
			"shareable_doctypes": set(),
			"sharing": {},
		}

		# "*" doc_events/permission_query_conditions/has_permission fire for
		# every doctype in the system, including ones belonging to other
		# apps' fixtures being imported earlier in the very migrate that is
		# adding this doctype's own new fields - at that point this table
		# genuinely does not have them yet. Degrade to "nothing is shareable
		# right now" rather than crash a migrate that has nothing to do with
		# this app at all.
		if not frappe.get_meta("Prymage Multi Company Settings").has_field("shareable_doctypes"):
			return empty

		# Same reasoning, belt and braces: the field existing is not the only
		# way loading this singleton can fail mid-migrate (confirmed by a
		# real crash here once already - a *different* child table this
		# doctype still had live rows in, whose own controller had just been
		# removed, blew up loading those rows, not this one missing a
		# field). The "*" wildcard this is called from fires on every insert
		# in the whole system, including Frappe's own internal bookkeeping
		# doctypes (Patch Log, observed directly) - there is no bounded set
		# of "normal" failure modes to enumerate here, so this catches
		# broadly and logs rather than enumerate them one at a time as each
		# is discovered the hard way.
		#
		# frappe.log_error(), not used here on purpose: it inserts an Error
		# Log *document*, which fires this same "*" before_insert hook again
		# for doctype="Error Log" - is_shareable() -> get_settings() -> the
		# same failure -> log_error() again -> infinite recursion, confirmed
		# directly (a real RecursionError, not a hypothetical). A plain
		# Python logger never creates a document, so it cannot re-enter this
		# hook at all.
		try:
			doc = frappe.get_cached_doc("Prymage Multi Company Settings")
			shareable_doctypes = {row.document_type for row in doc.shareable_doctypes if row.document_type}
			sharing: dict[str, dict[str, bool]] = {}
			for row in doc.company_doctype_sharing:
				sharing.setdefault(row.company, {})[row.document_type] = bool(row.is_shared)
		except Exception:
			frappe.logger("prymage").error("multi company settings failed to load", exc_info=True)
			return empty

		return {
			"enabled": bool(doc.enable_company_data_isolation),
			"require_selection": bool(doc.require_company_selection_on_login),
			"skip_single": bool(doc.skip_selection_for_single_company),
			"allow_multiple": bool(doc.allow_multiple_company_selection),
			"shareable_doctypes": shareable_doctypes,
			"sharing": sharing,
		}

	return frappe._dict(frappe.cache.get_value(CACHE_KEY, _load) or _load())


def is_shareable(doctype: str) -> bool:
	"""Does this doctype participate in company data isolation at all?

	The one check every wildcard hook (``permission_query_conditions``,
	``has_permission``, ``before_insert`` on ``"*"``) makes first, so adding
	a DocType to the registry is the only thing that ever needs to change -
	hooks.py, permissions.py and tagging.py never do, for any doctype, ever
	added to the registry from here on.
	"""
	return doctype in get_settings().shareable_doctypes


def shares(settings: frappe._dict, company: str, doctype: str) -> bool:
	"""Does `company` participate in the shared pool for `doctype`?"""
	if doctype not in settings.shareable_doctypes:
		return False
	return bool(settings.sharing.get(company, {}).get(doctype))


def on_company_trash(doc, method=None):
	"""Drop a deleted company's sharing rows (and its company tags).

	Without this the rows linger and a deleted company keeps granting access
	to the shared pool. Master records tagged with it are released back to
	untagged, which is the shared state, so nothing becomes unreachable.
	"""
	settings = frappe.get_single("Prymage Multi Company Settings")
	rows = [row for row in settings.company_doctype_sharing if row.company != doc.name]
	if len(rows) != len(settings.company_doctype_sharing):
		settings.set("company_doctype_sharing", rows)
		settings.save(ignore_permissions=True)

	for row in settings.shareable_doctypes:
		doctype = row.document_type
		if doctype and frappe.get_meta(doctype).get_field("custom_owner_company"):
			frappe.db.set_value(
				doctype,
				{"custom_owner_company": doc.name},
				"custom_owner_company",
				None,
				update_modified=False,
			)

	from prymage.multi_company.session import clear_request_cache

	clear_settings_cache()
	clear_request_cache()
