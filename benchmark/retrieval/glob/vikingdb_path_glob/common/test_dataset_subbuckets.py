from vikingdb_path_glob.common import dataset
from vikingdb_path_glob.performance import step0_prepare_data


def test_scale_500_final_layout_has_exact_disjoint_subbuckets():
    paths = list(dataset.iter_bucket_relpaths("scale_500", 500, seed=42))

    expected = {"scale_10": 10, "scale_50": 50, "scale_100": 100, "scale_200": 200}
    for name, count in expected.items():
        assert sum(path.startswith(name + "/") for path in paths) == count

    assert len(paths) == 500
    assert len(set(paths)) == 500
    assert sum(not path.startswith(tuple(name + "/" for name in expected)) for path in paths) == 140


def test_scale_500_move_plan_matches_final_layout_without_touching_remainder():
    original = list(dataset.iter_original_bucket_relpaths("scale_500", 500, seed=42))
    moves = list(dataset.iter_scale_500_moves(500, seed=42))
    final = list(dataset.iter_bucket_relpaths("scale_500", 500, seed=42))

    assert len(moves) == 360
    assert [source for source, _target in moves] == original[:360]
    assert [target for _source, target in moves] == final[:360]
    assert final[360:] == original[360:]


def test_small_scale_ground_truth_is_relative_to_each_query_root(tmp_path):
    output = str(tmp_path)
    dataset.write_manifest_index(output, seed=42)
    manifest = dataset.bucket_manifest_path(output, "scale_500")
    with open(manifest, "w", encoding="utf-8") as file:
        for path in dataset.iter_bucket_relpaths("scale_500", 500, seed=42):
            file.write(path + "\n")

    for scale, expected_count in (("10", 10), ("50", 50), ("100", 100), ("200", 200)):
        paths = dataset.load_scale_relpaths(output, scale)
        assert len(paths) == expected_count
        assert all(not path.startswith(f"scale_{scale}/") for path in paths)


def test_scale_metadata_keeps_500_root_and_adds_nested_small_scales():
    scales = {scale: (bucket, count) for scale, bucket, count in dataset.SCALES}

    assert scales["10"] == ("scale_500/scale_10", 10)
    assert scales["50"] == ("scale_500/scale_50", 50)
    assert scales["100"] == ("scale_500/scale_100", 100)
    assert scales["200"] == ("scale_500/scale_200", 200)
    assert scales["500"] == ("scale_500", 500)


def test_in_memory_scale_paths_match_nested_query_roots():
    assert len(list(dataset.iter_scale_relpaths("10", seed=42))) == 10
    assert len(list(dataset.iter_scale_relpaths("50", seed=42))) == 50
    assert len(list(dataset.iter_scale_relpaths("100", seed=42))) == 100
    assert len(list(dataset.iter_scale_relpaths("200", seed=42))) == 200
    assert len(list(dataset.iter_scale_relpaths("500", seed=42))) == 500
    assert all(
        not path.startswith("scale_10/") for path in dataset.iter_scale_relpaths("10", seed=42)
    )


def test_prepare_data_index_keeps_nested_scale_counts(tmp_path):
    (tmp_path / "manifest").mkdir()
    step0_prepare_data._write_index(
        str(tmp_path), seed=42, buckets=list(dataset.SCALE_BUCKETS), total=dataset.TOTAL_FILES
    )

    index = dataset.load_manifest_index(str(tmp_path))
    scales = {entry["scale"]: entry["count"] for entry in index["scales"]}
    assert {scale: scales[scale] for scale in ("10", "50", "100", "200")} == {
        "10": 10,
        "50": 50,
        "100": 100,
        "200": 200,
    }


def test_smoke_scale_500_too_small_for_subbuckets_keeps_original_layout():
    original = list(dataset.iter_original_bucket_relpaths("scale_500", 1, seed=42))
    final = list(dataset.iter_bucket_relpaths("scale_500", 1, seed=42))

    assert final == original
