"""Prometheus-compatible metrics middleware for FastAPI."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Tuple

MetricKey = Tuple[str, str, str]
PathKey = Tuple[str, str]


@dataclass
class LatencyStats:
    """Aggregate latency metrics for a route."""

    count: int = 0
    total_duration: float = 0.0

    def observe(self, duration: float) -> None:
        self.count += 1
        self.total_duration += duration


_request_counts: Dict[MetricKey, int] = defaultdict(int)
_error_counts: Dict[MetricKey, int] = defaultdict(int)
_latency_stats: Dict[PathKey, LatencyStats] = defaultdict(LatencyStats)
_metrics_lock = threading.Lock()


class MetricsMiddleware:
    """ASGI middleware that records request metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[override]
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        start_time = time.perf_counter()
        status_holder: Dict[str, int] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = time.perf_counter() - start_time
            _record_metrics(method, path, 500, duration, failed=True)
            raise
        else:
            duration = time.perf_counter() - start_time
            status_code = status_holder.get("status", 500)
            _record_metrics(
                method, path, status_code, duration, failed=status_code >= 500
            )


def _record_metrics(
    method: str, path: str, status: int, duration: float, *, failed: bool
) -> None:
    key: MetricKey = (method, path, str(status))
    path_key: PathKey = (method, path)
    with _metrics_lock:
        _request_counts[key] += 1
        _latency_stats[path_key].observe(duration)
        if failed:
            _error_counts[key] += 1


def render_metrics() -> str:
    """Render collected metrics in the Prometheus exposition format."""

    lines: list[str] = []

    lines.append("# HELP fcs_requests_total Total HTTP requests")
    lines.append("# TYPE fcs_requests_total counter")
    with _metrics_lock:
        for (method, path, status), value in sorted(_request_counts.items()):
            lines.append(
                f'fcs_requests_total{{method="{method}",path="{path}",status="{status}"}} {value}'
            )

        lines.append(
            "# HELP fcs_request_errors_total HTTP requests that resulted in errors"
        )
        lines.append("# TYPE fcs_request_errors_total counter")
        if _error_counts:
            for (method, path, status), value in sorted(_error_counts.items()):
                lines.append(
                    f'fcs_request_errors_total{{method="{method}",path="{path}",status="{status}"}} {value}'
                )
        else:
            lines.append('fcs_request_errors_total{method="",path="",status=""} 0')

        lines.append(
            "# HELP fcs_request_duration_seconds_sum Total time spent handling requests"
        )
        lines.append("# TYPE fcs_request_duration_seconds_sum counter")
        for (method, path), stats in sorted(_latency_stats.items()):
            lines.append(
                f'fcs_request_duration_seconds_sum{{method="{method}",path="{path}"}} {stats.total_duration}'
            )

        lines.append(
            "# HELP fcs_request_duration_seconds_count Total number of timed requests"
        )
        lines.append("# TYPE fcs_request_duration_seconds_count counter")
        for (method, path), stats in sorted(_latency_stats.items()):
            lines.append(
                f'fcs_request_duration_seconds_count{{method="{method}",path="{path}"}} {stats.count}'
            )

    return "\n".join(lines) + "\n"
