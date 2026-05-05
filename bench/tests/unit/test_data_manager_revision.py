from pathlib import Path
import importlib


def _fake_snapshot_download(*, local_dir: str, revision: str | None, **_kwargs):
    normal_dir = Path(local_dir)
    (normal_dir / "BDS").mkdir(parents=True, exist_ok=True)
    (normal_dir / "A").mkdir(parents=True, exist_ok=True)
    (normal_dir / "X").mkdir(parents=True, exist_ok=True)
    (normal_dir / "BDS" / "fresh.csv").write_text(
        f"revision,{revision}\n", encoding="utf-8"
    )
    (normal_dir / "A" / "fresh.txt").write_text("a\n", encoding="utf-8")
    (normal_dir / "X" / "fresh.py").write_text("x = 1\n", encoding="utf-8")
    return str(normal_dir)


def _seed_stale_normal_cache(cache_dir: Path) -> None:
    stale = cache_dir / "normal" / "BDEX"
    stale.mkdir(parents=True)
    (stale / "stale.csv").write_text("old\n", encoding="utf-8")
    (cache_dir / "normal" / ".hf_revision").write_text(
        "old-revision", encoding="utf-8"
    )


def test_server_data_manager_refreshes_normal_cache_on_revision_change(
    tmp_path, monkeypatch
):
    from server import data_manager

    data_manager = importlib.reload(data_manager)
    cache_dir = tmp_path / "hf_cache"
    _seed_stale_normal_cache(cache_dir)
    monkeypatch.setattr(
        data_manager, "_ensure_docs", lambda cache_dir, hf_repo, revision: "docs"
    )
    monkeypatch.setattr(data_manager, "snapshot_download", _fake_snapshot_download)

    paths = data_manager.ensure_data(
        series="normal",
        cache_dir=cache_dir,
        hf_repo="repo",
        revision="new-revision",
        need_reference=False,
    )

    bdex_dir = cache_dir / "normal" / "BDEX"
    assert paths.data_search_dirs[0] == str(bdex_dir)
    assert not (bdex_dir / "stale.csv").exists()
    assert (bdex_dir / "fresh.csv").exists()
    assert (cache_dir / "normal" / ".hf_revision").read_text(
        encoding="utf-8"
    ) == "new-revision"


def test_scripts_data_manager_refreshes_normal_cache_on_revision_change(
    tmp_path, monkeypatch
):
    from scripts import data_manager

    cache_dir = tmp_path / "hf_cache"
    _seed_stale_normal_cache(cache_dir)
    monkeypatch.setattr(
        data_manager, "_ensure_docs", lambda cache_dir, hf_repo, revision: "docs"
    )
    monkeypatch.setattr(data_manager, "snapshot_download", _fake_snapshot_download)

    paths = data_manager.ensure_data(
        series="normal",
        cache_dir=cache_dir,
        hf_repo="repo",
        revision="new-revision",
        need_reference=False,
    )

    bdex_dir = cache_dir / "normal" / "BDEX"
    assert paths.data_search_dirs[0] == str(bdex_dir)
    assert not (bdex_dir / "stale.csv").exists()
    assert (bdex_dir / "fresh.csv").exists()
    assert (cache_dir / "normal" / ".hf_revision").read_text(
        encoding="utf-8"
    ) == "new-revision"
