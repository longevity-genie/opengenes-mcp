"""Offline regression tests for the publication-count reference SQL.

Run with: python -m unittest discover -s test -p test_publication_ranking.py -v
No MCP server, downloaded dataset, or LLM credentials are required.
"""

import json
from pathlib import Path
import sqlite3
import unittest


class PublicationRankingTests(unittest.TestCase):
    def setUp(self):
        entries = json.loads(Path(__file__).with_name("test_qa.json").read_text(encoding="utf-8"))
        # Keep the regression tied to the SQL actually used by the reference QA.
        self.sql = next(
            entry["sql"] for entry in entries
            if entry["question"].startswith("Which gene")
            and "longevity_associations" in entry["sql"]
            and "COUNT(" in entry["sql"]
        )
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE longevity_associations (HGNC TEXT, doi TEXT)")

    def rank(self, rows):
        self.connection.executemany("INSERT INTO longevity_associations VALUES (?, ?)", rows)
        return self.connection.execute(self.sql).fetchall()

    def test_multiple_association_rows_are_one_publication(self):
        self.assertEqual(self.rank([
            ("FOXO3", "10.1/a"), ("FOXO3", "10.1/a"), ("FOXO3", "10.1/a"),
            ("APOE", "10.1/b"), ("APOE", "10.1/c"),
        ]), [("APOE", 2)])

    def test_all_ties_are_returned_in_gene_order(self):
        self.assertEqual(self.rank([
            ("FOXO3", "10.1/a"), ("APOE", "10.1/b"), ("OTHER", "10.1/c"),
            ("FOXO3", "10.1/d"), ("APOE", "10.1/e"),
        ]), [("APOE", 2), ("FOXO3", 2)])

    def test_doi_case_and_surrounding_spaces_do_not_inflate_count(self):
        self.assertEqual(self.rank([
            ("FOXO3", " 10.1/ABC "), ("FOXO3", "10.1/abc"),
        ]), [("FOXO3", 1)])

    def test_missing_dois_are_not_publications(self):
        self.assertEqual(self.rank([
            ("FOXO3", None), ("FOXO3", ""), ("FOXO3", "   "),
            ("APOE", "10.1/a"),
        ]), [("APOE", 1)])

    def test_empty_table_has_no_winner(self):
        self.assertEqual(self.rank([]), [])


if __name__ == "__main__":
    unittest.main()
