// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Prymage Ledger Statement", {
	refresh(frm) {
		frm.add_custom_button(__("Fetch Entries"), () => fetch_entries(frm)).addClass(
			frm.doc.entries && frm.doc.entries.length ? "" : "btn-primary"
		);

		frm.set_df_property("customer", "only_select", false);

		if (frm.doc.entries && frm.doc.entries.length && frm.doc.generated_on) {
			frm.set_intro(
				__("{0} entries, generated {1}. Use Print to produce the statement.", [
					frm.doc.entries.length,
					frappe.datetime.str_to_user(frm.doc.generated_on),
				]),
				"blue"
			);
		} else {
			frm.set_intro(__("Choose the party and the period, then Fetch Entries."), "orange");
		}
	},

	statement_type(frm) {
		// Clear the other two party fields so a stale value can never print.
		const keep = { Customer: "customer", Supplier: "supplier", Account: "account" }[
			frm.doc.statement_type
		];
		["customer", "supplier", "account"].forEach((field) => {
			if (field !== keep) frm.set_value(field, null);
		});
		frm.set_value("entries", []);
	},

	company(frm) {
		if (frm.doc.company) {
			frappe.db.get_value("Company", frm.doc.company, "default_letter_head").then((r) => {
				if (r.message && r.message.default_letter_head) {
					frm.set_value("letter_head", r.message.default_letter_head);
				}
			});
		}
	},

	onload(frm) {
		frm.set_query("account", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));

		if (frm.is_new()) {
			if (!frm.doc.company) {
				frm.set_value("company", frappe.defaults.get_user_default("Company"));
			}
			if (!frm.doc.from_date) {
				const year_start = frappe.datetime.get_today().slice(0, 4) + "-01-01";
				frm.set_value("from_date", year_start);
				frm.set_value("to_date", frappe.datetime.get_today());
			}
		}
	},
});

function fetch_entries(frm) {
	frm.call({
		doc: frm.doc,
		method: "fetch_entries",
		freeze: true,
		freeze_message: __("Reading the general ledger..."),
	}).then((r) => {
		frm.refresh_fields();

		// Save straight away. Print renders the *stored* document, so a user who
		// fetched and then printed without saving would get a statement with a
		// correct-looking header and an empty table.
		return frm.save().then(() => {
			if (r && r.message) {
				frappe.show_alert({
					message: __("{0} entries loaded.", [r.message.rows]),
					indicator: r.message.rows ? "green" : "orange",
				});
			}
		});
	});
}
