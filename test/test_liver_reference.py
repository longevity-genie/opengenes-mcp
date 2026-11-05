"""Offline checks that the liver-intervention reference preserves evidence context."""

import json
from pathlib import Path
import sqlite3
import unittest


class LiverReferenceTests(unittest.TestCase):
    def setUp(self):
        entries = json.loads(Path(__file__).with_name("test_qa.json").read_text(encoding="utf-8"))
        self.reference = next(
            entry for entry in entries
            if entry["question"] == "Are there any liver-specific interventions that increase lifespan in mice?"
        )
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""CREATE TABLE lifespan_change (
            HGNC TEXT, model_organism TEXT, sex TEXT, intervention_method TEXT,
            tissue TEXT, control_cohort_size REAL, experiment_cohort_size REAL,
            intervention_way TEXT, drug TEXT, lifespan_percent_change_mean REAL,
            lifespan_percent_change_median REAL, lifespan_percent_change_max REAL,
            significance_mean INTEGER, significance_median INTEGER,
            significance_max INTEGER, effect_on_lifespan TEXT, doi TEXT
        )""")

    def insert(self, **changes):
        record = dict(
            HGNC="IGF1", model_organism="mouse", sex="female",
            intervention_method="gene knockout", tissue="liver",
            control_cohort_size=25, experiment_cohort_size=17,
            intervention_way="combined (inducible mutation)", drug="tamoxifen",
            lifespan_percent_change_mean=None, lifespan_percent_change_median=2.9,
            lifespan_percent_change_max=3.9, significance_mean=None,
            significance_median=0, significance_max=0,
            effect_on_lifespan="increases lifespan", doi="10.1111/acel.12188",
        )
        record.update(changes)
        columns = ", ".join(record)
        placeholders = ", ".join("?" for _ in record)
        self.connection.execute(
            f"INSERT INTO lifespan_change ({columns}) VALUES ({placeholders})",
            tuple(record.values()),
        )

    def query(self):
        return [dict(row) for row in self.connection.execute(self.reference["sql"])]

    def test_positive_but_nonsignificant_record_keeps_metric_flags(self):
        self.insert()
        row = self.query()[0]
        self.assertEqual(row.get("lifespan_percent_change_median"), 2.9)
        self.assertEqual(row.get("significance_median"), 0)
        self.assertEqual(row.get("lifespan_percent_change_max"), 3.9)
        self.assertEqual(row.get("significance_max"), 0)

    def test_cohort_and_provenance_are_available_for_source_review(self):
        self.insert()
        row = self.query()[0]
        self.assertEqual(row.get("sex"), "female")
        self.assertEqual(row.get("doi"), "10.1111/acel.12188")
        self.assertEqual(row.get("control_cohort_size"), 25)
        self.assertEqual(row.get("experiment_cohort_size"), 17)
        self.assertEqual(row.get("drug"), "tamoxifen")
        self.assertEqual(row.get("intervention_way"), "combined (inducible mutation)")

    def test_missing_significance_is_not_replaced_by_a_positive_flag(self):
        self.insert(significance_median=1, significance_max=None)
        row = self.query()[0]
        self.assertIn("significance_max", row)
        self.assertIsNone(row["significance_max"])
        self.assertEqual(row.get("significance_median"), 1)

    def test_missing_mean_does_not_hide_median_extension_or_its_order(self):
        self.insert()
        self.insert(doi="10.1007/s11357-017-9971-0",
                    lifespan_percent_change_median=16.1,
                    lifespan_percent_change_max=2.5, significance_median=1,
                    significance_max=None)
        rows = self.query()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.get("lifespan_percent_change_median") for row in rows], [16.1, 2.9])

    def test_filter_excludes_other_species_tissues_and_reductions(self):
        self.insert()
        self.insert(HGNC="OTHER_SPECIES", model_organism="rat")
        self.insert(HGNC="OTHER_TISSUE", tissue="brain")
        self.insert(HGNC="REDUCTION", effect_on_lifespan="decreases lifespan")
        self.assertEqual([row["HGNC"] for row in self.query()], ["IGF1"])


if __name__ == "__main__":
    unittest.main()
