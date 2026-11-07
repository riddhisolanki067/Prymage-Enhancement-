// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt

frappe.query_reports["Prymage Multi Company Audit Trail"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "changed_by",
			label: __("Changed By"),
			fieldtype: "Link",
			options: "User",
		},
	],
};
