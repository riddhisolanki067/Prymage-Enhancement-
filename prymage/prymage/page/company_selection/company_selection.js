// Copyright (c) 2026, Riddhi and contributors
// For license information, please see license.txt
//
// A1 - the screen a user lands on after login. Only the companies assigned to
// them are listed; picking one (or several, when that is allowed) stores the
// choice in the session defaults every ERPNext form and report already reads.

frappe.pages["company-selection"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Select Company"),
		single_column: true,
	});

	wrapper.company_selection = new prymage.CompanySelection(page);
};

frappe.pages["company-selection"].on_page_show = function (wrapper) {
	wrapper.company_selection && wrapper.company_selection.load();
};

frappe.provide("prymage");

prymage.CompanySelection = class CompanySelection {
	constructor(page) {
		this.page = page;
		this.selected = new Set();
		this.$body = $('<div class="prymage-company-selection">').appendTo(this.page.main);
		this.load();
	}

	load() {
		this.$body.html(
			`<div class="text-muted" style="padding:2rem 0">${__("Loading companies...")}</div>`
		);

		frappe
			.call("prymage.multi_company.session.get_company_options")
			.then((r) => {
				this.data = r.message || {};
				this.selected = new Set(this.data.selected || []);
				this.render();
			})
			.catch(() => {
				this.$body.html(
					`<div class="text-danger" style="padding:2rem 0">${__(
						"Could not load the company list."
					)}</div>`
				);
			});
	}

	render() {
		const { companies = [], allow_multiple, full_name } = this.data;

		if (!companies.length) {
			this.$body.html(`
				<div class="text-center" style="padding:3rem 0">
					<h4>${__("No company assigned")}</h4>
					<p class="text-muted">${__(
						"Your account is not assigned to any company. Ask your system administrator to add one under Company Access on your user."
					)}</p>
				</div>
			`);
			return;
		}

		const hint = allow_multiple
			? __("Pick one or more companies. The first one you pick supplies the defaults on new documents.")
			: __("Pick the company you want to work in.");

		this.$body.html(`
			<div style="max-width:720px;margin:0 auto;padding:1rem 0 3rem">
				<h3 style="margin-bottom:0.25rem">${__("Welcome {0}", [frappe.utils.escape_html(full_name || "")])}</h3>
				<p class="text-muted" style="margin-bottom:1.5rem">${hint}</p>
				<div class="prymage-company-list"></div>
				<div style="margin-top:1.5rem;display:flex;gap:0.5rem;align-items:center">
					<button class="btn btn-primary btn-sm prymage-continue">${__("Continue")}</button>
					<span class="text-muted small prymage-selection-count"></span>
				</div>
			</div>
		`);

		const $list = this.$body.find(".prymage-company-list");
		companies.forEach((company) => $list.append(this.company_row(company, allow_multiple)));

		this.$body.find(".prymage-continue").on("click", () => this.submit());
		$list.on("click", ".prymage-company-card", (e) => {
			this.toggle($(e.currentTarget).attr("data-company"), allow_multiple);
		});

		this.refresh_state();
	}

	company_row(company, allow_multiple) {
		const subtitle = [company.abbr, company.default_currency, company.country]
			.filter(Boolean)
			.join(" &middot; ");

		return $(`
			<div class="prymage-company-card" data-company="${frappe.utils.escape_html(company.name)}"
				style="display:flex;align-items:center;gap:0.75rem;border:1px solid var(--border-color);
				border-radius:var(--border-radius-md);padding:0.75rem 1rem;margin-bottom:0.5rem;cursor:pointer">
				<input type="${allow_multiple ? "checkbox" : "radio"}" name="prymage-company"
					style="margin:0;pointer-events:none">
				<div style="flex:1">
					<div style="font-weight:600">${frappe.utils.escape_html(
						company.company_name || company.name
					)}</div>
					<div class="text-muted small">${subtitle}</div>
				</div>
			</div>
		`);
	}

	toggle(company, allow_multiple) {
		if (!allow_multiple) {
			this.selected = new Set([company]);
		} else if (this.selected.has(company)) {
			this.selected.delete(company);
		} else {
			this.selected.add(company);
		}
		this.refresh_state();
	}

	refresh_state() {
		this.$body.find(".prymage-company-card").each((_i, el) => {
			const $el = $(el);
			const on = this.selected.has($el.attr("data-company"));
			$el.find("input").prop("checked", on);
			$el.css({
				"border-color": on ? "var(--primary)" : "var(--border-color)",
				"background-color": on ? "var(--bg-light-gray)" : "transparent",
			});
		});

		this.$body.find(".prymage-continue").prop("disabled", this.selected.size === 0);
		this.$body
			.find(".prymage-selection-count")
			.text(
				this.selected.size > 1
					? __("{0} companies selected", [this.selected.size])
					: ""
			);
	}

	submit() {
		const companies = Array.from(this.selected);
		if (!companies.length) return;

		frappe
			.call({
				method: "prymage.multi_company.session.select_companies",
				args: { companies: companies },
				freeze: true,
				freeze_message: __("Opening {0}...", [companies[0]]),
			})
			.then(() => {
				// The visibility rules are applied server side, so the desk has
				// to be reloaded rather than routed - caches, boot info and the
				// sidebar all have to be rebuilt for the new company.
				window.location.href = "/app";
			});
	}
};
