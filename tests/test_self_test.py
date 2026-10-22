from photometry_workbench.self_test import run


def test_self_test_passes(tmp_path):
    report = tmp_path / "self-test.txt"
    assert run(str(report)) == 0, report.read_text()
    assert report.read_text().strip().endswith("PASSED")
