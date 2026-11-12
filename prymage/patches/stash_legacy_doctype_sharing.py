"""Reads the old per-doctype fixed-Check-column sharing table
(``Prymage Company Data Sharing``) before the schema sync this same migrate
is about to run drops it - its module files are going away in the same
change that introduces the new company x doctype matrix table.

Registered in ``patches.txt`` under ``[pre_model_sync]``, which Frappe runs
*before* ``frappe.model.sync.sync_all()`` (confirmed directly in
``frappe/migrate.py`` - patches, then schema sync/orphan-doctype removal,
then ``after_migrate`` hooks). The new matrix table does not exist yet at
this point either (schema sync has not run), so the data cannot be written
to its final home from here - it is stashed in cache instead, for
``prymage/install.py``'s ``_migrate_legacy_sharing_table()`` (running from
the ``after_migrate`` hook, well after schema sync has created the new
tables) to pick up and write for real.
"""

import json

import frappe

STASH_CACHE_KEY = "prymage_legacy_sharing_migration_stash"


def execute():
	if not frappe.db.table_exists("Prymage Company Data Sharing"):
		return

	rows = frappe.db.sql(
		"""
		select company, share_customers, share_suppliers, share_items,
			share_price_lists, share_contacts_and_addresses
		from `tabPrymage Company Data Sharing`
		where parent = 'Prymage Multi Company Settings'
		""",
		as_dict=True,
	)
	if rows:
		frappe.cache.set_value(STASH_CACHE_KEY, json.dumps(rows), expires_in_sec=3600)

	# Not just a read: the rows must actually be gone before this function
	# returns, not merely copied. Something elsewhere in this very migrate
	# (confirmed: Frappe's own patch-log bookkeeping, inserting a "Patch
	# Log" document right after this patch runs) can still try to fully
	# load the "Prymage Multi Company Settings" singleton before schema
	# sync has run - and loading it tries to instantiate every child row it
	# still has, including these, whose controller module is gone in this
	# same change. Deleting them here, inside the one patch guaranteed to
	# run before anything else touches this table, is what actually
	# prevents that crash - the cache stash above is a separate concern
	# (handing the data to post-schema-sync code), not a backup of these
	# rows for this purpose.
	frappe.db.sql(
		"""
		delete from `tabPrymage Company Data Sharing`
		where parent = 'Prymage Multi Company Settings'
		"""
	)
	frappe.db.commit()
