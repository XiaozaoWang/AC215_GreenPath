"""Local development regressions; these files are excluded from the image."""

from pathlib import Path
import sys
import unittest

import h3
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 让测试找到上一层的 cli.py
from cli import (
    SpatialConfig, aerial_candidates, classify_street, detect, spatial_candidates, street_candidates,
)


class RuleChecks(unittest.TestCase):
    def test_street_context_and_negation(self):
        examples = {
            "A parking lot with buildings and little vegetation.": "KEEP",
            "A street approaching the tunnel entrance.": "KEEP",
            "The image is under a bridge. Surrounding streets remain visible.": "KEEP",
            "The camera is beneath the overpass.": "EXCLUDE",
            "The image was captured inside a parking garage. Outdoor streets remain visible.": "EXCLUDE",
            "The image was not taken inside a garage.": "KEEP",
            "The view is not obstructed. The image was captured inside a tunnel.": "EXCLUDE",
            "The image was captured inside the covered underground parking garages.": "EXCLUDE",
            "The photograph is within a shop.": "EXCLUDE",
            "The camera is positioned from inside of the store.": "EXCLUDE",
            "The image is not taken inside the covered parking structures.": "KEEP",
            "The camera is beneath the bridges. Surrounding streets are clearly visible.": "KEEP",
            "Tunnel walls obscure most of the outdoor scene.": "EXCLUDE",
            "The view is severely blocked by the concrete ceilings.": "EXCLUDE",
            "": "KEEP",
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(classify_street(text)[0], expected)
        self.assertEqual(classify_street(None)[0], "KEEP")

    def test_minimum_requires_one_observed_score(self):
        tiles = pd.DataFrame({
            "cell_id": ["minimum", "unavailable", "not_minimum"],
            "a": [1, 0, 1], "b": [0, np.nan, 2], "c": [np.nan, 0, 1],
            "open_landscape": [1, np.nan, 1],
        })
        before = tiles.copy(deep=True)
        result = aerial_candidates(tiles, ["a", "b", "c", "open_landscape"])
        self.assertEqual(result.cell_id.tolist(), ["minimum"])
        pd.testing.assert_frame_equal(before, tiles)

    def test_additional_tunnel_filter_matches_f1(self):
        descriptions = [
            "A dark tunnel is visible ahead; surrounding streets remain clear.",
            "A dark tunnel is visible ahead; surrounding streets remain clear.",
            "A dark tunnel-like canopy of trees covers the road.",
            "A parking lot with dark buildings.",
            "The camera is inside a garage.",
            "A garage is not inside the view; the outdoor scene is clear.",
        ]
        images = pd.DataFrame({"image_id": range(len(descriptions)), "rationale": descriptions,
                               "usable": [True, False, True, True, True, True]})
        before = images.copy(deep=True)
        result = street_candidates(images)
        extra = result.loc[result.reason.str.contains("extra tunnel check", regex=False)]
        self.assertEqual(extra.image_id.tolist(), [0, 4, 5])
        # The extra tunnel check matches broadly on purpose, even when Rule 1 says KEEP.
        self.assertEqual(classify_street(descriptions[0])[0], "KEEP")
        both = result.loc[result.image_id.eq(4)]
        self.assertEqual(len(both), 1)
        self.assertEqual(both.candidate_type.iloc[0], "street_scene_keyword")
        self.assertIn("camera inside enclosed space", both.reason.iloc[0])
        self.assertIn("extra tunnel check", both.reason.iloc[0])
        pd.testing.assert_frame_equal(before, images)

    def test_featureless_aerial_threshold_and_missing_scores(self):
        dimensions = [f"score_{i}" for i in range(15)] + ["open_landscape"]
        tiles = pd.DataFrame(1.0, index=range(5), columns=dimensions)
        tiles["cell_id"] = ["featureless", "too_few", "wrong_open", "minimum", "unavailable"]
        tiles.loc[0:2, "open_landscape"] = [5, 5, 4]
        tiles.loc[0, "score_0"] = 0  # Fourteen 1s + one unavailable + open=5 qualifies.
        tiles.loc[1, ["score_0", "score_1"]] = [0, np.nan]  # Only thirteen 1s.
        tiles.loc[4, dimensions] = np.nan
        before = tiles.copy(deep=True)
        result = aerial_candidates(tiles, dimensions)
        self.assertEqual(result.loc[result.candidate_type.eq("misread_aerial"), "cell_id"].tolist(), ["featureless"])
        self.assertEqual(result.loc[result.candidate_type.eq("aerial_all_minimum"), "cell_id"].tolist(), ["minimum"])
        pd.testing.assert_frame_equal(before, tiles)

    def neighborhood(self, count=6, target=0.0, area_type="suburb"):
        center = h3.latlng_to_cell(42.4, -72.5, 10)
        ring = sorted(set(h3.grid_disk(center, 1)) - {center})[:count]
        return pd.DataFrame([
            {"area_id": "target_area" if index == 0 else "neighbor_area",
             "cell_id": f"cell-{index}", "h3_index": index_h3, "tile_path": f"tile-{index}",
             "area_type": area_type if index == 0 else "city",
             "grassland": target if index == 0 else 1.0}
            for index, index_h3 in enumerate([center, *ring])
        ])

    def test_spatial_scope_signed_gap_and_missing_neighbors(self):
        config = SpatialConfig(dimensions=("grassland",))
        cells = self.neighborhood(target=0.25)
        before = cells.copy(deep=True)
        result = spatial_candidates(cells, config)
        self.assertEqual(result.cell_id.tolist(), ["cell-0"])
        self.assertEqual(result.score_difference.iloc[0], 0.75)
        self.assertEqual(result.n_neighbors.iloc[0], 6)
        pd.testing.assert_frame_equal(before, cells)
        self.assertTrue(spatial_candidates(self.neighborhood(area_type="city"), config).empty)
        self.assertTrue(spatial_candidates(self.neighborhood(target=1.0), config).empty)
        cells.loc[1:3, "grassland"] = np.nan
        self.assertTrue(spatial_candidates(cells, config).empty)

    def test_overlapping_locations_do_not_inflate_neighbor_count(self):
        cells = self.neighborhood(count=3)
        copies = cells.iloc[1:].assign(area_id="overlapping_area")
        copies["cell_id"] += "-copy"
        result = spatial_candidates(pd.concat([cells, copies], ignore_index=True),
                                    SpatialConfig(dimensions=("grassland",)))
        self.assertTrue(result.empty)

    def test_zero_candidates_keep_a_usable_schema(self):
        street = pd.DataFrame(columns=["rationale", "usable", "image_id", "cell_id", "area_id"])
        tiles = pd.DataFrame(columns=["cell_id", "tile_path", "grassland", "open_landscape"])
        cells = self.neighborhood(target=1.0)
        result = detect(street, tiles, cells, ["grassland"], ["grassland"],
                        SpatialConfig(dimensions=("grassland",)))
        self.assertTrue(result.empty)
        self.assertEqual(str(result.image_id.dtype), "string")
        self.assertEqual(str(result.n_neighbors.dtype), "Int64")


if __name__ == "__main__":
    unittest.main()
