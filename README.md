## Prymage

ERPNext enhancements for Prymage, built from the client-approved
**ERPNext Enhancement** requirement document:

| Group | Requirement |
|-------|-------------|
| **A** | Multi-company management — company selection screen after login, company access per user, a Multi-Company Settings screen, company tagging on shared master records, visibility rules applied in list views / link fields / reports, and an audit trail of sharing changes |
| **B** | Cloud subscription expiry — settings screen, countdown from 30 days before expiry at every login, and access restriction for everyone but the Administrator once expired (with a safety release) |
| **C** | Withholding tax calculated on the amount excluding VAT, at the invoice and on advance payments |
| **D** | Customer, Supplier and Account ledger statement print formats, each with its own company's letterhead and logo |

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
| A4 Company tagging | *Company Ownership* section on Customer/Supplier/Item/Price List/Contact/Address | `prymage/multi_company/tagging.py`, `prymage/custom_fields.py` |
| A5 Visibility rules | — | `prymage/multi_company/permissions.py` |
| A6 Audit trail | **Prymage Multi Company Audit Trail** report | `prymage/prymage/report/prymage_multi_company_audit_trail/` (built on `track_changes`, which stays on) |
| B Subscription expiry | **Prymage Subscription Settings** | `prymage/subscription/guard.py` |
| C Withholding tax ex-VAT | *Withholding Tax on Net of VAT* section on Payment Entry | `prymage/overrides/payment_entry.py`, `prymage/overrides/purchase_invoice.py` |
| D Ledger statements | **Prymage Ledger Statement** + 3 print formats | `prymage/prymage/doctype/prymage_ledger_statement/` |

### Verifying a figure

The ledger statement is built from GL Entry using the same rules ERPNext's own
**General Ledger** report uses. To check a statement, run the General Ledger
report with the same company, party and period — the totals should match
exactly.

#### License

mit
