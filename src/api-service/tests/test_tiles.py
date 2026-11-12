from io import BytesIO
from unittest.mock import Mock

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from google.api_core.exceptions import Forbidden, NotFound

from api import tiles

CELLS = [h3.latlng_to_cell(42.36, -71.06, 10), h3.latlng_to_cell(42.37, -71.07, 10)]


@pytest.fixture
def blob(monkeypatch):
    tiles._load_cells.cache_clear()
    client = Mock()
    monkeypatch.setattr(tiles.storage, "Client", client)
    yield client.return_value.bucket.return_value.blob.return_value
    tiles._load_cells.cache_clear()


def parquet(cells):
    buffer = BytesIO()
    pq.write_table(pa.table({"h3_index": cells}), buffer)
    return buffer.getvalue()


def test_gcs_parquet_order_cache_and_path(blob, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "organic-victory-508017-v9")
    monkeypatch.setenv("GREENPATH_BUCKET", "ac215-notebook")
    monkeypatch.setenv("GREENPATH_DATASET", "1026_Full_data")
    blob.download_as_bytes.return_value = parquet([*CELLS, CELLS[0]])
    first = tiles.get_tiles()
    assert [tile.h3_index for tile in first] == CELLS
    first[0].h3_index = "changed"
    assert [tile.h3_index for tile in tiles.get_tiles()] == CELLS
    tiles.storage.Client.return_value.bucket.return_value.blob.assert_called_once_with(
        "datasets/1026_Full_data/cells/city_ma_boston_big.parquet")
    blob.download_as_bytes.assert_called_once()


@pytest.mark.parametrize("error,expected", [(NotFound("missing"), tiles.UnknownRegionError),
                                           (Forbidden("denied"), tiles.RegionLoadError)])
def test_gcs_errors_and_retry(blob, error, expected):
    blob.download_as_bytes.side_effect = error
    with pytest.raises(expected):
        tiles.get_tiles()
    blob.download_as_bytes.side_effect = None
    blob.download_as_bytes.return_value = parquet(CELLS)
    assert len(tiles.get_tiles()) == 2


@pytest.mark.parametrize("data", [b"invalid parquet", parquet(["invalid h3"])])
def test_bad_data(blob, data):
    blob.download_as_bytes.return_value = data
    with pytest.raises(tiles.RegionLoadError):
        tiles.get_tiles()


def test_region_path_validation(blob):
    with pytest.raises(tiles.UnknownRegionError):
        tiles.get_tiles("../other")
    blob.download_as_bytes.assert_not_called()
