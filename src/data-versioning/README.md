# GreenPath data versioning

Snapshots a training set (table + aerial tiles) with DVC, so the exact data behind a training run can be pulled back later.
Based on the tutorial [dlops-io/data-versioning](https://github.com/dlops-io/data-versioning). Not part of the Vertex pipeline, run it by hand after a new training set is made.
`cli.py` downloads `training/<id>/` (`dataset.parquet`, `meta.json`, `exclusions.parquet`) and every tile in the table's `tile_path` into `greenpath_dataset/` (not in git). Git keeps only `greenpath_dataset.dvc` and `.dvc/config`.

## First time (already done in this repo, see `.dvc/config`)

```sh
sh docker-shell.sh                                                     # on the Mac, in src/data-versioning/
dvc init --subdir                                                      # in the container
dvc remote add -d greenpath_dataset gs://$GREENPATH_BUCKET/dvc_store
```

## Save a version (in the container)

```sh
python cli.py --training-id <id>
dvc add greenpath_dataset
dvc push
```

Then on the Mac: `git add src/data-versioning`, commit, `git tag -a dataset_<id> -m "training/<id>"`, and push the commit and the tag.

## Get a version back

`git checkout <tag>` on the Mac (for example `dataset_<id>`), then `dvc pull` in the container. Check out your branch again when done.
