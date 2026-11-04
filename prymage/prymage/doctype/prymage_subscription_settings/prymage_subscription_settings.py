# Copyright (c) 2026, Riddhi and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, nowdate

CACHE_KEY = "prymage_subscription_settings"

#: Role that may see and edit these settings. Deliberately left unassigned on
#: install, which means only the Administrator account can reach the screen
#: (Administrator implicitly holds every role). Assign the role to delegate.
ADMIN_ROLE = "Prymage Subscription Administrator"

#: site_config.json key that releases the access restriction no matter what the
#: expiry date says. The client can never be locked out permanently.
BYPASS_KEY = "prymage_subscription_bypass"


class PrymageSubscriptionSettings(Document):
	def validate(self):
		if self.reminder_days is None or self.reminder_days < 0:
			frappe.throw(_("Start Reminding (Days Before Expiry) cannot be negative."))

		if self.restrict_access_after_expiry and not self.expiry_date:
			frappe.throw(
				_(
					"Set an Expiry Date before turning on Restrict Access After Expiry, "
					"otherwise the restriction has no date to work from."
				)
			)

	def on_update(self):
		clear_subscription_cache()


def clear_subscription_cache():
	frappe.cache.delete_value(CACHE_KEY)


def get_settings() -> frappe._dict:
	"""Cached settings. Read on every login and on every desk boot."""

	def _load():
		doc = frappe.get_cached_doc("Prymage Subscription Settings")
		return {
			"expiry_date": str(doc.expiry_date) if doc.expiry_date else None,
			"reminder_days": int(doc.reminder_days or 0),
			"restrict_access_after_expiry": bool(doc.restrict_access_after_expiry),
			"provider_name": doc.provider_name or "",
			"provider_contact": doc.provider_contact or "",
			"reminder_message": doc.reminder_message or "",
			"expired_message": doc.expired_message or "",
		}

	return frappe._dict(frappe.cache.get_value(CACHE_KEY, _load) or _load())


def get_status(on_date: str | None = None) -> frappe._dict:
	"""Evaluate the subscription against a date. Pure function of the settings.

	Returns ``active=None`` when the feature is not configured at all, so every
	caller can tell "not in use" apart from "not expired".
	"""
	settings = get_settings()
	on_date = getdate(on_date or nowdate())

	status = frappe._dict(
		configured=bool(settings.expiry_date),
		expiry_date=settings.expiry_date,
		days_remaining=None,
		expired=False,
		show_reminder=False,
		severity="info",
		message="",
		restrict=False,
		bypassed=bool(frappe.conf.get(BYPASS_KEY)),
		provider_name=settings.provider_name,
		provider_contact=settings.provider_contact,
	)

	if not status.configured:
		return status

	expiry = getdate(settings.expiry_date)
	# Positive while the package is still live, 0 on the expiry date itself,
	# negative once it has passed. The package is usable *on* the expiry date.
	days_remaining = date_diff(expiry, on_date)
	status.days_remaining = days_remaining
	status.expired = days_remaining < 0

	fmt = {
		"days": abs(days_remaining),
		"date": frappe.format(expiry, {"fieldtype": "Date"}),
		"provider": settings.provider_name or _("your ERPNext service provider"),
	}

	if status.expired:
		status.show_reminder = True
		status.severity = "expired"
		status.message = _format(settings.expired_message, fmt)
		status.restrict = bool(settings.restrict_access_after_expiry) and not status.bypassed
	elif days_remaining <= (settings.reminder_days or 0):
		status.show_reminder = True
		status.message = _format(settings.reminder_message, fmt)
		# The notification "becomes more prominent as the date approaches".
		if days_remaining <= 7:
			status.severity = "critical"
		elif days_remaining <= 14:
			status.severity = "warning"
		else:
			status.severity = "info"

	return status


def _format(template: str, values: dict) -> str:
	"""Render the admin-editable message without letting a stray brace throw."""
	if not template:
		return ""
	out = template
	for key, value in values.items():
		out = out.replace("{" + key + "}", str(value))
	return out
