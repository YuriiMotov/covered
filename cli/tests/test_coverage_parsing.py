"""
Tests for `_get_coverage_info` - parsing coverage value from an HTML report.
"""

from pathlib import Path

from covered.cli import _get_coverage_info


def _write_index(directory: Path, html: str) -> None:
    (directory / "index.html").write_text(html)


def test_get_coverage_info_returns_none_when_index_missing(tmp_path: Path):
    """
    If the directory has no `index.html`, return `None`.
    """
    assert _get_coverage_info(tmp_path) is None


def test_get_coverage_info_parses_pc_cov_span_template(tmp_path: Path):
    """
    Extract the float value from `<span class="pc_cov">…%</span>`.
    """
    _write_index(tmp_path, '<span class="pc_cov">87.5%</span>')

    assert _get_coverage_info(tmp_path) == 87.5


def test_get_coverage_info_returns_none_when_no_pattern_matches(tmp_path: Path):
    """
    `index.html` exists but contains no recognised template - return `None`.
    """
    _write_index(tmp_path, "<html><body>no coverage info here</body></html>")

    assert _get_coverage_info(tmp_path) is None


def test_get_coverage_info_handles_decimal_values(tmp_path: Path):
    """
    Values like `87.42` are parsed as `float`, not truncated to int.
    """
    _write_index(tmp_path, '<span class="pc_cov">87.42%</span>')

    result = _get_coverage_info(tmp_path)

    assert result == 87.42
    assert isinstance(result, float)


def test_get_coverage_info_handles_integer_values(tmp_path: Path):
    """
    Values like `100` are parsed as `100.0`.
    """
    _write_index(tmp_path, '<span class="pc_cov">100%</span>')

    result = _get_coverage_info(tmp_path)

    assert result == 100.0
    assert isinstance(result, float)
