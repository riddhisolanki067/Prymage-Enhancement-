// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt
//
// Loaded on every desk page (app_include_js).
//  * B2 - shows the subscription countdown once per login.
//  * A1 - sends a resumed session that has no company selected to the
//    selection screen, and shows the active company in the navbar.

frappe.provide("prymage");

// Every feature runs inside its own guard. jQuery does not catch exceptions
// thrown by an event handler: one escaping from an "app_ready" handler aborts
// the whole dispatch chain, so Frappe's own startup listeners - the workspace
// sidebar and the socket.io connection among them - never run. Nothing in this
// file is allowed to escape.
prymage.run_safely = function (label, fn) {
	try {
		fn();
	} catch (e) {
		console.error("prymage: " + label + " failed", e);
	}
};

$(document).on("app_ready", function () {
	prymage.run_safely("subscription notice", prymage.show_subscription_notice);

	// The current route is NOT known yet at app_ready. desk.js calls the async
	// set_route() and fires app_ready on the very next line, so
	// frappe.router.current_route is still null and frappe.get_route_str()
	// (which is current_route.join("/")) throws. The router triggers its own
	// "change" event after the route has been parsed, which is the first moment
	// it is safe to read.
	// The navbar is not in the DOM yet at app_ready either, so the pill is
	// rendered on the same event. Both are cheap and both no-op once done.
	if (frappe.router && frappe.router.on) {
		frappe.router.on("change", function () {
			prymage.run_safely("company selection", prymage.enforce_company_selection);
			prymage.run_safely("active company pill", prymage.show_active_company);
		});
	}
});

prymage.show_subscription_notice = function () {
	const info = frappe.boot && frappe.boot.prymage_subscription;
	if (!info || !info.message) return;

	// "once after every successful login": sessionStorage is per browser tab
	// session, which is the closest thing the desk has to "this login".
	const key = "prymage_subscription_notice:" + (info.expiry_date || "") + ":" + frappe.session.user;
	if (sessionStorage.getItem(key)) return;
	sessionStorage.setItem(key, "1");

	const titles = {
		expired: __("Subscription Expired"),
		critical: __("Subscription Expiring"),
		warning: __("Subscription Expiring"),
		info: __("Subscription Reminder"),
	};
	const indicators = { expired: "red", critical: "red", warning: "orange", info: "blue" };

	let message = frappe.utils.escape_html(info.message);
	if (info.provider_contact) {
		message +=
			'<br><br><span class="text-muted">' +
			__("Contact: {0}", [frappe.utils.escape_html(info.provider_contact)]) +
			"</span>";
	}
	if (info.expired && info.is_admin && info.restricted) {
		message +=
			'<br><br><b class="text-danger">' +
			__("Everyone except the Administrator is being stopped at login.") +
			"</b>";
	}
	if (info.bypassed) {
		message +=
			'<br><br><span class="text-muted">' +
			__("The access restriction is released by prymage_subscription_bypass in site_config.json.") +
			"</span>";
	}

	// The last week before expiry, and after it, is a modal nobody can miss.
	// Earlier than that it is a banner that does not block work.
	if (info.severity === "expired" || info.severity === "critical") {
		frappe.msgprint({
			title: titles[info.severity],
			message: message,
			indicator: indicators[info.severity],
		});
	} else {
		frappe.show_alert(
			{ message: message, indicator: indicators[info.severity] || "blue" },
			15
		);
	}
};

prymage.enforce_company_selection = function () {
	const info = frappe.boot && frappe.boot.prymage_multi_company;
	if (!info || !info.selection_pending) return;

	// Read current_route directly rather than through frappe.get_route_str(),
	// which assumes it is already populated.
	const route = (frappe.router && frappe.router.current_route) || [];
	if (route[0] === "company-selection") return;

	// Deferred out of the router's change handler. Calling set_route from
	// inside it re-enters routing while the current route is still rendering,
	// which leaves Frappe's sidebar and form rendering half-initialised.
	// No loop is possible: this only fires again for a route that is not the
	// selection screen, and set_route lands us on it.
	setTimeout(function () {
		prymage.run_safely("company selection redirect", function () {
			frappe.set_route("company-selection");
		});
	}, 0);
};

prymage.show_active_company = function () {
	const info = frappe.boot && frappe.boot.prymage_multi_company;
	if (!info || !info.require_selection) return;
	if (!info.selected_companies || !info.selected_companies.length) return;
	if (document.querySelector(".prymage-active-company")) return;

	const label =
		info.selected_companies.length > 1
			? info.selected_companies[0] + " +" + (info.selected_companies.length - 1)
			: info.selected_companies[0];

	// A1 also asks that the user can switch company "at any time without
	// logging out again". Frappe v16 renders Navbar Settings entries into the
	// sidebar header dropdown, whose markup moves between versions, so the
	// switch control is this pill rather than a menu entry we do not own. It
	// doubles as the indicator of which company is in force.
	const $pill = $(
		'<a class="prymage-active-company" href="/app/company-selection">' +
			'<span class="indicator-pill blue">' +
			frappe.utils.escape_html(label) +
			"</span></a>"
	)
		.attr("title", __("Switch company"))
		.css({ display: "flex", "align-items": "center", "margin-right": "8px" });

	// The desk shell moves between versions: v16 workspace pages have a left
	// sidebar and no top navbar at all, while a custom Page still renders the
	// older navbar. Try the known containers in order and simply do nothing if
	// none of them is there - the pill is an indicator, and the actual way to
	// switch company is the Switch Company entry in the sidebar menu.
	const containers = [
		[".body-sidebar-bottom", "prepend"],
		[".navbar > .flex", "prepend"],
		[".navbar", "append"],
	];

	for (const [selector, how] of containers) {
		const $target = $(selector).first();
		if ($target.length) {
			how === "prepend" ? $target.prepend($pill) : $target.append($pill);
			return;
		}
	}
};
