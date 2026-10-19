import numpy as np
from PIL import Image
import pytest
from greenpath_encoder.encode import load_pixels, PREPROCESSING


def test_center_crop_preserves_pixels_without_resizing(tmp_path):
    pixels = np.arange(256 * 256 * 3, dtype=np.uint32).reshape(256, 256, 3).astype(np.uint8)
    Image.fromarray(pixels).save(tmp_path / "tile.png")
    result, sources = load_pixels(tmp_path, [{"cell_id": "a", "tile_path": "tile.png"}], 224)
    np.testing.assert_array_equal(result[0], pixels[16:240, 16:240])
    assert len(sources["a"]["sha256"]) == 64
    assert PREPROCESSING["l2_normalization"] is False


@pytest.mark.parametrize("case", ["small_image", "duplicate_id", "path_escape", "empty"])
def test_invalid_image_inputs_fail_before_encoder_execution(tmp_path, case):
    Image.new("RGB", (100 if case == "small_image" else 256, 256)).save(tmp_path / "tile.png")
    rows = [{"cell_id": "a", "tile_path": "tile.png"}]
    if case == "duplicate_id": rows *= 2
    if case == "path_escape": rows[0]["tile_path"] = "../secret.png"
    if case == "empty": rows = []
    with pytest.raises(ValueError): load_pixels(tmp_path, rows, 224)
