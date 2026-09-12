from pathlib import Path

from context_loader import build_context, load_rcas, load_runbooks


def _write_rca(dir_: Path, inc_id: str, text: str) -> None:
    folder = dir_ / inc_id
    folder.mkdir(parents=True)
    (folder / "rca.md").write_text(text, encoding="utf-8")


def test_load_rcas_reads_a_well_formed_file(tmp_path: Path) -> None:
    _write_rca(
        tmp_path,
        "INC-900",
        "---\n"
        "id: INC-900\n"
        "title: things broke\n"
        "severity: SEV3\n"
        "services: [orders, inventory]\n"
        "category: Caching\n"
        "---\n"
        "## Summary\nit was the cache\n",
    )

    rcas = load_rcas(tmp_path)

    assert len(rcas) == 1
    assert rcas[0]["id"] == "INC-900"
    assert rcas[0]["services"] == ["orders", "inventory"]
    assert rcas[0]["body"] == "## Summary\nit was the cache"


def test_load_rcas_is_sorted_by_directory_name(tmp_path: Path) -> None:
    for inc_id in ("INC-902", "INC-900", "INC-901"):
        _write_rca(
            tmp_path,
            inc_id,
            f"---\nid: {inc_id}\ntitle: t\nseverity: SEV3\nservices: []\ncategory: c\n---\nbody\n",
        )

    rcas = load_rcas(tmp_path)

    assert [r["id"] for r in rcas] == ["INC-900", "INC-901", "INC-902"]


def test_load_rcas_skips_a_file_missing_the_closing_fence(tmp_path: Path) -> None:
    _write_rca(tmp_path, "INC-901", "---\nid: INC-901\n")

    assert load_rcas(tmp_path) == []


def test_load_rcas_skips_invalid_yaml(tmp_path: Path) -> None:
    _write_rca(tmp_path, "INC-902", "---\nid: [unclosed\n---\nbody\n")

    assert load_rcas(tmp_path) == []


def test_load_rcas_skips_a_file_missing_a_required_field(tmp_path: Path) -> None:
    _write_rca(tmp_path, "INC-903", "---\nid: INC-903\n---\nbody\n")

    assert load_rcas(tmp_path) == []


def test_load_rcas_skips_frontmatter_that_is_not_a_mapping(tmp_path: Path) -> None:
    _write_rca(tmp_path, "INC-904", "---\njust a plain string\n---\nbody\n")

    assert load_rcas(tmp_path) == []


def test_load_rcas_one_bad_file_does_not_lose_the_good_ones(tmp_path: Path) -> None:
    _write_rca(
        tmp_path,
        "INC-900",
        "---\nid: INC-900\ntitle: t\nseverity: SEV3\nservices: []\ncategory: c\n---\nbody\n",
    )
    _write_rca(tmp_path, "INC-901", "---\nid: INC-901\n")

    rcas = load_rcas(tmp_path)

    assert [r["id"] for r in rcas] == ["INC-900"]


def test_load_rcas_on_an_empty_directory(tmp_path: Path) -> None:
    assert load_rcas(tmp_path) == []


def test_load_runbooks_reads_every_markdown_file(tmp_path: Path) -> None:
    (tmp_path / "service-down.md").write_text("restart it", encoding="utf-8")
    (tmp_path / "slow-requests.md").write_text("check p95", encoding="utf-8")

    runbooks = load_runbooks(tmp_path)

    assert {r["name"] for r in runbooks} == {"service-down", "slow-requests"}
    by_name = {r["name"]: r["content"] for r in runbooks}
    assert by_name["service-down"] == "restart it"


def test_load_runbooks_on_an_empty_directory(tmp_path: Path) -> None:
    assert load_runbooks(tmp_path) == []


def test_build_context_includes_both_sections(tmp_path: Path) -> None:
    incidents_dir = tmp_path / "incidents"
    runbooks_dir = tmp_path / "runbooks"
    runbooks_dir.mkdir()
    _write_rca(
        incidents_dir,
        "INC-900",
        "---\nid: INC-900\ntitle: cache bug\nseverity: SEV2\nservices: [inventory]\n"
        "category: Caching\n---\nstale reads\n",
    )
    (runbooks_dir / "stale-catalogue-data.md").write_text("check the TTL", encoding="utf-8")

    context = build_context(incidents_dir, runbooks_dir)

    assert "INC-900" in context
    assert "stale reads" in context
    assert "stale-catalogue-data" in context
    assert "check the TTL" in context


def test_build_context_on_empty_directories_still_returns_a_string(tmp_path: Path) -> None:
    incidents_dir = tmp_path / "incidents"
    runbooks_dir = tmp_path / "runbooks"
    incidents_dir.mkdir()
    runbooks_dir.mkdir()

    context = build_context(incidents_dir, runbooks_dir)

    assert "no incidents have been closed" in context
    assert "no runbooks yet" in context
