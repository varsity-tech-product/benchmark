from scripts import gen_v3_data


def test_btc_reference_path_prefers_frozen_file(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen" / "BTCUSDT_1d_2021_2024.csv"
    fallback = tmp_path / "cache" / "BDEX" / "BTCUSDT_1d_2021_2024.csv"
    marker = tmp_path / "cache" / ".hf_revision"
    frozen.parent.mkdir(parents=True)
    fallback.parent.mkdir(parents=True)
    frozen.write_text("frozen\n", encoding="utf-8")
    fallback.write_text("fallback\n", encoding="utf-8")
    marker.write_text("old-revision", encoding="utf-8")
    monkeypatch.setattr(gen_v3_data, "BTC_REF", frozen)
    monkeypatch.setattr(gen_v3_data, "BTC_REF_FALLBACK", fallback)
    monkeypatch.setattr(gen_v3_data, "NORMAL_CACHE_REVISION", marker)
    monkeypatch.setattr(gen_v3_data, "DATASET_REVISION", "new-revision")

    assert gen_v3_data._btc_reference_path() == frozen


def test_btc_reference_path_accepts_matching_cache_revision(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen" / "BTCUSDT_1d_2021_2024.csv"
    fallback = tmp_path / "cache" / "BDEX" / "BTCUSDT_1d_2021_2024.csv"
    marker = tmp_path / "cache" / ".hf_revision"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("fallback\n", encoding="utf-8")
    marker.write_text("new-revision", encoding="utf-8")
    monkeypatch.setattr(gen_v3_data, "BTC_REF", frozen)
    monkeypatch.setattr(gen_v3_data, "BTC_REF_FALLBACK", fallback)
    monkeypatch.setattr(gen_v3_data, "NORMAL_CACHE_REVISION", marker)
    monkeypatch.setattr(gen_v3_data, "DATASET_REVISION", "new-revision")

    assert gen_v3_data._btc_reference_path() == fallback


def test_btc_reference_path_materializes_pinned_source_for_stale_cache_revision(
    tmp_path, monkeypatch
):
    frozen = tmp_path / "frozen" / "BTCUSDT_1d_2021_2024.csv"
    fallback = tmp_path / "cache" / "BDEX" / "BTCUSDT_1d_2021_2024.csv"
    marker = tmp_path / "cache" / ".hf_revision"
    downloaded = tmp_path / "downloaded" / "BTCUSDT_1d_2021_2024.csv"
    fallback.parent.mkdir(parents=True)
    downloaded.parent.mkdir(parents=True)
    fallback.write_text("fallback\n", encoding="utf-8")
    downloaded.write_text("downloaded\n", encoding="utf-8")
    marker.write_text("old-revision", encoding="utf-8")
    monkeypatch.setattr(gen_v3_data, "BTC_REF", frozen)
    monkeypatch.setattr(gen_v3_data, "BTC_REF_FALLBACK", fallback)
    monkeypatch.setattr(gen_v3_data, "NORMAL_CACHE_REVISION", marker)
    monkeypatch.setattr(gen_v3_data, "DATASET_REVISION", "new-revision")
    monkeypatch.setattr(
        gen_v3_data, "_materialize_btc_reference", lambda: downloaded
    )

    assert gen_v3_data._btc_reference_path() == downloaded
