"""Cloud subscription expiry notice and access restriction (requirement B).

B2 - the countdown. ``extend_bootinfo`` puts the evaluated status in
``frappe.boot.prymage_subscription`` and the desk script shows it once per
login, getting more prominent as the date approaches.

B3 - the restriction. Checked at login (``on_session_creation``), which is where
the requirement puts it: "everyone else is stopped at login with a renewal
message".

Three independent safety releases, because a wrong expiry date must never be
able to lock a client out permanently:

1. the Administrator account is never restricted;
2. ``"prymage_subscription_bypass": 1`` in ``site_config.json`` releases the
   restriction for everyone (needs no database access, so it works even when
   nobody can log in);
3. *Restrict Access After Expiry* is off by default and cannot be switched on
   without an expiry date.

``bench`` commands, the scheduler and background jobs never create a login
session, so site administration always stays reachable.
"""

import frappe
from frappe import _

from prymage.prymage.doctype.prymage_subscription_settings.prymage_subscription_settings import (
	ADMIN_ROLE,
	BYPASS_KEY,
	get_status,
)

SESSION_SHOWN_KEY = "prymage_subscription_notice_shown"


def is_exempt_user(user: str | None = None) -> bool:
	return (user or frappe.session.user) == "Administrator"


def on_session_creation():
	"""Stop a non-Administrator login once the package has expired."""
	if is_exempt_user():
		return

	if frappe.conf.get(BYPASS_KEY):
		return

	try:
		status = get_status()
	except Exception:
		# A broken or half-migrated settings record must not stop anyone from
		# logging in. Log it and let the login through.
		frappe.log_error(title="Prymage: subscription check failed")
		return

	if not status.restrict:
		return

	message = status.message or _(
		"Your ERPNext Hosting and Support Package has expired. "
		"Please contact your service provider to renew it."
	)

	_terminate_session()
	frappe.throw(message, title=_("Subscription Expired"), exc=frappe.AuthenticationError)


def _terminate_session():
	"""Destroy the session that was just created, then clear the cookies.

	``Session.start()`` commits the Sessions row before ``on_session_creation``
	runs, so simply raising here would leave a usable session id behind - the
	browser would be bounced back to the login page while the API still
	accepted that sid. ``frappe.local.login_manager`` cannot be used: it is only
	assigned after ``LoginManager.__init__`` returns, and this hook runs inside
	it.
	"""
	from frappe.auth import clear_cookies
	from frappe.sessions import clear_sessions, delete_session

	user = frappe.session.user
	reason = "Prymage: subscription expired"

	try:
		delete_session(frappe.session.sid, user=user, reason=reason)
		# Any session opened before the expiry date must go too, otherwise a
		# browser left logged in overnight keeps working.
		clear_sessions(user=user, force=True)
	finally:
		try:
			clear_cookies()
		except Exception:
			# No cookie manager (a non-HTTP login path): nothing to clear.
			pass
		frappe.db.commit()


def get_boot_payload() -> dict | None:
	"""What the desk needs to render the countdown. None when not configured."""
	try:
		status = get_status()
	except Exception:
		return None

	if not status.configured or not status.show_reminder:
		return None

	return {
		"message": status.message,
		"severity": status.severity,
		"days_remaining": status.days_remaining,
		"expiry_date": status.expiry_date,
		"expired": status.expired,
		"provider_name": status.provider_name,
		"provider_contact": status.provider_contact,
		"restricted": bool(status.restrict),
		"bypassed": bool(status.bypassed),
		"is_admin": is_exempt_user(),
	}


def settings_has_permission(doc, ptype=None, user=None, debug=False):
	"""Keep the settings screen to the subscription administrator role.

	The role is created on install and left unassigned, so out of the box only
	the Administrator account - which implicitly holds every role - can read or
	change these settings, and the doctype does not appear in anyone else's
	search. Assign the role to delegate it.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return ADMIN_ROLE in frappe.get_roles(user)
