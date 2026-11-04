// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Prymage Multi Company Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Sync Companies"), () => {
			frappe
				.call({
					method:
						"prymage.prymage.doctype.prymage_multi_company_settings.prymage_multi_company_settings.sync_companies",
					freeze: true,
					freeze_message: __("Syncing companies..."),
				})
				.then((r) => {
					if (r.message && r.message.added) {
						frm.reload_doc();
						frappe.show_alert({
							message: __("Missing companies added, sharing everything by default."),
							indicator: "green",
						});
					} else {
						frappe.show_alert({ message: __("Every company already has a row."), indicator: "blue" });
					}
				});
		});

		frm.set_intro(
			frm.doc.enable_company_data_isolation
				? __("Data isolation is ON. Users only see master records their selected companies are allowed to see.")
				: __("Data isolation is OFF. Every company sees every master record, exactly as standard ERPNext."),
			frm.doc.enable_company_data_isolation ? "orange" : "blue"
		);
	},
});
