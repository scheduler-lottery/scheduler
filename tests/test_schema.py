"""schema.sql is the database as it is now, for anyone reading it: every
column db.MIGRATIONS adds to older databases is in it too."""

import os
import re

import db

SCHEMA = open(os.path.join(os.path.dirname(db.__file__), "schema.sql")).read()


def _columns(table):
    found = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \((.*?)\n\);", SCHEMA, re.S)
    assert found, table
    return {m.group(1) for m in re.finditer(r"^\s+(\w+)\s+[A-Z]", found.group(1), re.M)}


def test_every_migrated_column_is_in_schema_sql():
    missing = [(table, column) for table, column, _ddl in db.MIGRATIONS if column not in _columns(table)]
    assert missing == []


def test_migrated_columns_match_their_schema_sql_type():
    for table, column, ddl in db.MIGRATIONS:
        line = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \(.*?^\s+" + column + r"\s+([^,\n]*?),?\s*(--|$)",
                         SCHEMA, re.S | re.M)
        assert line and line.group(1).strip() == ddl, (table, column, line and line.group(1))
