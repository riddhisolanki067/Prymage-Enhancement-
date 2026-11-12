## Prymage

ERPNext enhancements for Prymage, built from the client-approved
**ERPNext Enhancement** requirement document:

| Group | Requirement |
|-------|-------------|
| **A** | Multi-company management — company selection screen after login, company access per user, a Multi-Company Settings screen, company tagging on shared master records, visibility rules applied in list views / link fields / reports, and an audit trail of sharing changes |
| **B** | Cloud subscription expiry — settings screen, countdown from 30 days before expiry at every login, and access restriction for everyone but the Administrator once expired (with a safety release) |
| **C** | Withholding tax calculated on the amount excluding VAT, at the invoice and on advance payments |
| **D** | Customer, Supplier and Account ledger statement print formats, against ERPNext's own General Ledger report |

### Setup

```bash
bench --site <site> install-app prymage
bench --site <site> execute prymage.install.after_migrate   # re-run after a pull
bench build --app prymage
```

Data isolation ships **off**. Nothing changes until an administrator ticks
*Enable Company Data Isolation* in **Prymage Multi Company Settings**.

### Where each requirement lives

| Requirement | Doctype / screen | Code |
|---|---|---|
| A1 Company selection on login | **Select Company** page | `prymage/multi_company/session.py`, `prymage/public/js/prymage.bundle.js` |
| A2 Company access per user | *Company Access* tab on **User** | `prymage/multi_company/user_companies.py` |
| A3 Multi-Company Settings | **Prymage Multi Company Settings** | `prymage/prymage/doctype/prymage_multi_company_settings/` |
| A4 Company tagging | *Company Ownership* section, added to any DocType listed in the **Shareable DocTypes** table on Prymage Multi Company Settings | `prymage/multi_company/tagging.py`, `prymage/custom_fields.py` |
| A5 Visibility rules | — | `prymage/multi_company/permissions.py` |

**Adding a new shareable DocType needs no code.** Open **Prymage Multi Company
Settings**, add a row to *Shareable DocTypes* (the DocType, and optionally
which existing field on its form to add "Owned by Company" after — leave
blank to add it at the end), and Save. The field is created immediately, and
every company gets a sharing row for it, same save. `hooks.py` never changes:
`permission_query_conditions`, `has_permission` and `before_insert` are all
registered once under the `"*"` wildcard doctype key (Frappe dispatches
`hooks.get(doctype, []) + hooks.get("*", [])` for all three — confirmed
directly in `frappe/model/db_query.py` and `frappe/permissions.py`), gated by
a single cached `is_shareable()` membership check so every other doctype in
the system pays only one in-memory set lookup for this, not a new entry here.
Removing a row stops that DocType from participating (its sharing rows are
dropped) but does **not** delete the "Owned by Company" field itself — that
would drop the column, and every value already stored in it, so it is left
in place deliberately; retire it by hand (see `RETIRED_FIELDS` in
`custom_fields.py`) if it should actually go.
| A6 Audit trail | **Prymage Multi Company Audit Trail** report | `prymage/prymage/report/prymage_multi_company_audit_trail/` (built on `track_changes`, which stays on) |
| B Subscription expiry | **Prymage Subscription Settings** | `prymage/subscription/guard.py` |
| C Withholding tax ex-VAT | *Withholding Tax on Net of VAT* section on Payment Entry | `prymage/overrides/payment_entry.py`, `prymage/overrides/purchase_invoice.py` |
| C withholding split on the ledger | monkey-patches ERPNext's **General Ledger** report `execute()` | `prymage/overrides/general_ledger_report.py` |
| D Ledger statements | 3 Print Formats (`print_format_for: Report`, bound to the **General Ledger** report itself), selected from its own Print dialog | `prymage/fixtures/print_format.json` |

### Printing a ledger statement

No custom doctype: run ERPNext's own **General Ledger** report (Accounting > General Ledger),
filter by Supplier / Customer / Account as usual, **set Group By to "Categorize by Voucher
(Consolidated)"** (one continuous Opening/.../Closing, matching the client's own reference layout
exactly - the report's own default groups per voucher instead, which prints as several small
Opening/Closing fragments), then **Print** and pick **Prymage Supplier Ledger Statement** /
**Prymage Customer Ledger Statement** / **Prymage Account Ledger Statement** from the Print
Format dropdown. Layout (title, From/To, ID/Name/Address, the 8 columns, Remarks rows, the
closing Total/Balance lines, ₵ formatting with parentheses for a negative balance) matches the
client's own reference images exactly - verified against them directly, not approximated. A
withholding-tax payment shows as two lines - net payment and "Withholding Tax Deducted" -
automatically, on screen and in the printout; this is done by patching the report's own
`execute()` (see `general_ledger_report.py`'s own docstring for why, and the explicit tradeoff
that was accepted), which also adds cheque number, mode of payment and remarks to every row - none
of which the stock report selects by default - needed to match the reference layout. Letterhead is
the report print dialog's own standard "With Letterhead" picker - not automatic per company; pick
the right one for whichever company you're printing.

Each one is `print_format_for: Report`, `report: General Ledger`, `print_format_type: JS` - the
native Frappe fields for a report-bound print format, not `doc_type`. Report-bound Print Formats
are rendered client-side against the report's own `data`/`filters`/`columns` by a JS micro-template
engine, not server Jinja2 (`||` not `or`) - and `print_format_for: Report` also means Frappe's own
Print dialog only ever offers these three when printing the General Ledger report itself; they
don't show up anywhere else, by design, not just by convention.

Shipped as a fixture (`prymage/fixtures/print_format.json`), not a module-folder `standard: Yes`
doctype: Frappe's own `PrintFormat.before_save()` forces `standard = "No"` on any
`print_format_for: Report` record the moment it is saved, which the module-folder sync mechanism
(`reload_doc`) does not manage.

#### License

mit
