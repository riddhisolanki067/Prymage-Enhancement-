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
```

Data isolation ships **off**. Nothing changes until an administrator ticks
*Enable Company Data Isolation* in **Prymage Multi Company Settings**.

### Verification

```bash
bench --site <site> execute prymage.tests.verify.run
```

Builds its own fixtures, exercises every requirement and asserts the ledger
statements against ERPNext's own General Ledger report.

#### License

mit
