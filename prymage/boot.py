"""Everything the desk needs on page load.

Kept to one small dict so the boot payload stays cheap: both settings reads are
served from the Redis cache.
"""

import frappe

from prymage.multi_company.session import (
	get_allowed_companies,
	get_selected_companies,
)
from prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings import (
	get_settings as get_multi_company_settings,
)
from prymage.subscription.guard import get_boot_payload as get_subscription_payload


def extend_bootinfo(bootinfo):
	try:
		bootinfo.prymage_subscription = get_subscription_payload()
	except Exception:
		bootinfo.prymage_subscription = None
		frappe.log_error(title="Prymage: subscription boot failed")

	try:
		settings = get_multi_company_settings()
		allowed = get_allowed_companies()
		selected = get_selected_companies()
		bootinfo.prymage_multi_company = {
			"isolation_enabled": settings.enabled,
			"require_selection": settings.require_selection,
			"allow_multiple": settings.allow_multiple,
			"allowed_companies": allowed,
			"selected_companies": selected,
			# The desk sends the user to the selection screen when a session
			# resumed without one - a browser left open from before the setting
			# was switched on, for instance.
			"selection_pending": bool(
				settings.require_selection and len(allowed) > 1 and not selected
			),
		}
	except Exception:
		bootinfo.prymage_multi_company = None
		frappe.log_error(title="Prymage: multi company boot failed")
