
from agent.transcript_capture.gbrain_verify import discover_flat_txt, verify_corpus_shape


def test_discover_flat_txt_is_flat_only(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("b")
    assert discover_flat_txt(tmp_path) == [tmp_path / "a.txt"]


def test_verify_corpus_shape_reports_nested_txt_and_part_violations(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "bad.part").write_text("part")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("b")
    result = verify_corpus_shape(tmp_path)
    assert not result["ok"]
    assert result["flat_txt_count"] == 1
    assert result["nested_txt_count"] == 1
    assert result["corpus_part_count"] == 1
