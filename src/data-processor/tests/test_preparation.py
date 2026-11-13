"""Small preparation regressions; not included in the deployed image."""

from pathlib import Path
import tempfile
import sys
import unittest

import h3
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so the tests can find cli.py one folder up
from cli import (
    PreparationConfig, assign_splits, quantile_map, write_prepared,
)


class PreparationChecks(unittest.TestCase):
    def test_quantile_midpoint_ties_clipping_and_missing(self):
        result = quantile_map(np.array([1., 1., 3., 5.]), np.array([1., 2., 4., 5.]),
                              np.array([1., 3., 5., np.nan, 0., 6.]))
        np.testing.assert_allclose(result, [1.75, 3.75, 4.625, np.nan, 1., 5.], equal_nan=True)

    def test_spatial_places_and_blocks_stay_together(self):
        locations = [("test", 42.0, -72.0), ("near_test", 42.001, -72.001),
                     ("development", 43.0, -73.0), ("development", 43.0001, -73.0001)]
        cells = pd.DataFrame([{"area_id": area, "cell_id": f"cell-{i}", "lat": lat, "lon": lon,
                               "h3_index": h3.latlng_to_cell(lat, lon, 10)}
                              for i, (area, lat, lon) in enumerate(locations)])
        config = PreparationConfig(test_areas=("test",))
        first = assign_splits(cells, config)
        pd.testing.assert_frame_equal(first, assign_splits(cells, config))
        self.assertTrue(first.loc[first.area_id.isin(["test", "near_test"]), "split"].eq("test").all())
        self.assertTrue(first.loc[first.area_id.eq("development"), "split"].ne("test").all())
        self.assertTrue(first.loc[first.split.eq("test"), "fold"].isna().all())
        self.assertEqual(first.loc[first.area_id.eq("development"), "block_id"].nunique(), 1)
        self.assertEqual(first.loc[first.area_id.eq("development"), "split"].nunique(), 1)
        self.assertNotIn("split", cells.columns)

    def test_unknown_heldout_area_fails(self):
        cells = pd.DataFrame({"area_id": ["existing"], "cell_id": ["cell"], "lat": [42.], "lon": [-72.],
                              "h3_index": [h3.latlng_to_cell(42., -72., 10)]})
        with self.assertRaisesRegex(ValueError, "Unknown Test areas"):
            assign_splits(cells, PreparationConfig(test_areas=("existing", "missing")))
        # None of the Test places are there (small trial dataset): no Test split
        self.assertTrue(assign_splits(cells, PreparationConfig(test_areas=("missing",))).split.ne("test").all())

    def test_prepared_outputs_never_replace_existing_directory(self):
        result = (pd.DataFrame({"cell_id": ["cell"]}), pd.DataFrame({"cell_id": []}), {"training_id": "test"})
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "prepared"
            write_prepared(result, str(output))
            self.assertEqual({p.name for p in output.iterdir()}, {"dataset.parquet", "exclusions.parquet", "meta.json"})
            with self.assertRaises(FileExistsError):
                write_prepared(result, str(output))


if __name__ == "__main__":
    unittest.main()
