"""性能报告的纯函数测试，不依赖启动服务或机器性能。"""
from __future__ import annotations

import json

import pytest

from tests.performance_test import summarize_samples, write_report


def test_summarize_samples_uses_existing_nearest_rank_p95_semantics():
    summary = summarize_samples([4.0, 1.0, 3.0, 2.0])

    assert summary == {
        "count": 4,
        "mean_ms": 2.5,
        "min_ms": 1.0,
        "max_ms": 4.0,
        "p95_ms": 4.0,
    }
    assert isinstance(summary["count"], int)


def test_summarize_samples_rejects_empty_input():
    with pytest.raises(ValueError, match="空性能样本"):
        summarize_samples([])


def test_write_report_creates_parent_and_writes_valid_json_atomically(tmp_path):
    target = write_report(tmp_path / "nested" / "report.json", {"schema_version": 1})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"schema_version": 1}
    assert not target.with_suffix(".json.tmp").exists()
