// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Prymage Subscription Settings", {
	refresh(frm) {
		if (!frm.doc.expiry_date) {
			frm.set_intro(
				__("No expiry date set, so no reminders are shown and nobody is ever restricted."),
				"blue"
			);
			return;
		}

		const today = frappe.datetime.get_today();
		const days = frappe.datetime.get_day_diff(frm.doc.expiry_date, today);
		let colour = "blue";
		let text;

		if (days < 0) {
			colour = "red";
			text = __("Expired {0} day(s) ago.", [Math.abs(days)]);
			if (frm.doc.restrict_access_after_expiry) {
				text += " " + __("Everyone except the Administrator is being stopped at login.");
			}
		} else {
			if (days <= 7) colour = "red";
			else if (days <= 14) colour = "orange";
			text = __("{0} day(s) remaining.", [days]);
			if (days <= (frm.doc.reminder_days || 0)) {
				text += " " + __("Users are seeing the countdown at login.");
			} else {
				text +=
					" " +
					__("The countdown starts {0} day(s) before expiry.", [frm.doc.reminder_days || 0]);
			}
		}

		frm.set_intro(text, colour);
	},
});
