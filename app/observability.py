"""请求关联与轻量级运行时指标。

模块只使用标准库，避免为了基础观测能力引入新的运行时依赖。请求 ID
保存在 ``ContextVar``，因此领域服务和事件总线可以在不改变每个方法
签名的前提下，保留同一条请求的关联信息。
"""
from __future__ import annotations

import re
import threading
import uuid
from collections import Counter, defaultdict
from contextvars import ContextVar, Token


REQUEST_ID_HEADER = "X-Request-ID"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_trace_id: ContextVar[str | None] = ContextVar("blackboard_trace_id", default=None)


def request_trace_id(value: str | None) -> str:
    """采用安全的外部请求 ID；无效或缺失时创建新的 UUID。"""
    if value and _TRACE_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def set_trace_id(trace_id: str) -> Token[str | None]:
    """为当前异步上下文设置关联 ID，并返回用于恢复的 token。"""
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """恢复调用前的关联 ID，防止请求间串线。"""
    _trace_id.reset(token)


def current_trace_id() -> str | None:
    """返回当前请求的关联 ID；后台任务没有请求上下文时为 ``None``。"""
    return _trace_id.get()


HTTP_DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _labels(labels: dict[str, str]) -> str:
    return ",".join(
        f'{key}="{value}"' for key, value in sorted(labels.items())
    )


class RequestMetrics:
    """进程内 Prometheus 风格请求计数和延迟直方图。

    标签只采用固定的 HTTP method、路由模板和状态码，绝不包含 query 参数
    或业务 ID，避免指标基数随用户输入无限增长。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Counter[tuple[str, str, str]] = Counter()
        self._duration_sum: defaultdict[tuple[str, str, str], float] = defaultdict(float)
        self._duration_buckets: Counter[tuple[str, str, str, float]] = Counter()

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        key = (method, route, str(status_code))
        with self._lock:
            self._requests[key] += 1
            self._duration_sum[key] += duration_seconds
            for upper_bound in HTTP_DURATION_BUCKETS:
                if duration_seconds <= upper_bound:
                    self._duration_buckets[(*key, upper_bound)] += 1
            self._duration_buckets[(*key, float("inf"))] += 1

    def render_prometheus(self) -> list[str]:
        """渲染已完成请求的快照；当前 ``/metrics`` 请求在响应后才入账。"""
        with self._lock:
            requests = dict(self._requests)
            duration_sum = dict(self._duration_sum)
            duration_buckets = dict(self._duration_buckets)

        lines = [
            "# HELP blackboard_http_requests_total Completed HTTP requests.",
            "# TYPE blackboard_http_requests_total counter",
        ]
        for method, route, status in sorted(requests):
            labels = _labels({"method": method, "route": route, "status": status})
            lines.append(f"blackboard_http_requests_total{{{labels}}} {requests[(method, route, status)]}")

        lines.extend((
            "# HELP blackboard_http_request_duration_seconds HTTP request latency.",
            "# TYPE blackboard_http_request_duration_seconds histogram",
        ))
        for method, route, status in sorted(requests):
            labels = {"method": method, "route": route, "status": status}
            for upper_bound in (*HTTP_DURATION_BUCKETS, float("inf")):
                bucket_labels = _labels({
                    **labels,
                    "le": "+Inf" if upper_bound == float("inf") else str(upper_bound),
                })
                value = duration_buckets.get((method, route, status, upper_bound), 0)
                lines.append(
                    "blackboard_http_request_duration_seconds_bucket"
                    f"{{{bucket_labels}}} {value}"
                )
            label_text = _labels(labels)
            lines.append(
                "blackboard_http_request_duration_seconds_sum"
                f"{{{label_text}}} {duration_sum[(method, route, status)]}"
            )
            lines.append(
                "blackboard_http_request_duration_seconds_count"
                f"{{{label_text}}} {requests[(method, route, status)]}"
            )
        return lines

    def reset(self) -> None:
        """仅供测试隔离指标快照。"""
        with self._lock:
            self._requests.clear()
            self._duration_sum.clear()
            self._duration_buckets.clear()


request_metrics = RequestMetrics()
