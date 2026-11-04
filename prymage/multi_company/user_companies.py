"""Company access per user (requirement A2).

The *Allowed Companies* grid on the User form is the editing surface for the
User Permission records on Company. User Permissions are the mechanism that
actually restricts the data - the selection screen on its own only sets a
default - so the grid writes real User Permission rows rather than inventing a
parallel permission model.

``onload`` refreshes the grid from the User Permission records every time the
form is opened, which runs server side in ``frappe.desk.form.load.getdoc``, so
the browser receives the refreshed rows as the canonical document and the form
does not come up dirty. A User Permission added by hand in the Role Permission
screens therefore shows up in the grid rather than being silently dropped on
the next save.
"""

import frappe

ALLOWED_FIELD = "custom_allowed_companies"


def get_company_permissions(user: str) -> list[dict]:
	return frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		fields=["name", "for_value", "is_default"],
		order_by="for_value",
	)


def load_allowed_companies(doc, method=None):
	"""Fill the grid from the User Permission records when the form opens."""
	if not doc.meta.get_field(ALLOWED_FIELD):
		return

	doc.set(ALLOWED_FIELD, [])
	for row in get_company_permissions(doc.name):
		doc.append(ALLOWED_FIELD, {"company": row.for_value, "is_default": row.is_default})


def sync_allowed_companies(doc, method=None):
	"""Write the grid back to User Permission records."""
	if not doc.meta.get_field(ALLOWED_FIELD):
		return

	wanted = _read_grid(doc)
	existing = {row.for_value: row for row in get_company_permissions(doc.name)}

	for company, is_default in wanted.items():
		row = existing.get(company)
		if row:
			if bool(row.is_default) != is_default:
				frappe.db.set_value("User Permission", row.name, "is_default", int(is_default))
			continue

		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": doc.name,
				"allow": "Company",
				"for_value": company,
				"is_default": int(is_default),
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)

	for company, row in existing.items():
		if company not in wanted:
			frappe.delete_doc("User Permission", row.name, ignore_permissions=True, force=True)

	if wanted or existing:
		# User Permissions are cached per user; without this the user keeps the
		# old company list until the cache happens to expire.
		frappe.clear_cache(user=doc.name)
		from prymage.multi_company.session import clear_request_cache

		clear_request_cache()


def _read_grid(doc) -> dict[str, bool]:
	wanted: dict[str, bool] = {}
	for row in doc.get(ALLOWED_FIELD) or []:
		if not row.company:
			continue
		if row.company in wanted:
			frappe.throw(
				frappe._("Company {0} is listed twice under Allowed Companies.").format(
					frappe.bold(row.company)
				)
			)
		wanted[row.company] = bool(row.is_default)

	if sum(1 for is_default in wanted.values() if is_default) > 1:
		frappe.throw(frappe._("Only one allowed company can be the default."))

	return wanted
