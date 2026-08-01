"""Unit tests for the arq worker's priority lanes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import get_settings
from app.services.queue.models import JobPriority
from app.services.queue.worker import (
    QueueLane,
    WorkerSettings,
    build_worker,
    get_queue_lanes,
)


def _fake_settings(**overrides) -> SimpleNamespace:
    base = {
        "queue_name": "test_queue",
        "queue_max_concurrency": 3,
        "queue_high_concurrency": 1,
        "queue_low_concurrency": 1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestQueueLanes:
    """The set of consumed queues must match the set of enqueued queues."""

    def test_every_priority_has_a_lane(self) -> None:
        """A priority without a lane means those jobs are queued but never run.

        QueueService.enqueue_job routes to f"{queue_name}:{priority.value}", so
        the lanes have to cover every JobPriority member.
        """
        settings = get_settings()
        expected = {f"{settings.queue_name}:{p.value}" for p in JobPriority}

        assert {lane.queue_name for lane in get_queue_lanes()} == expected

    def test_normal_lane_keeps_queue_max_concurrency(self) -> None:
        """The everyday lane keeps the documented concurrency setting."""
        with patch(
            "app.services.queue.worker.get_settings",
            return_value=_fake_settings(queue_max_concurrency=5),
        ):
            lanes = {lane.priority: lane for lane in get_queue_lanes()}

        assert lanes[JobPriority.NORMAL].max_jobs == 5
        assert lanes[JobPriority.NORMAL].queue_name == "test_queue:normal"

    def test_high_and_low_lanes_are_separately_configurable(self) -> None:
        with patch(
            "app.services.queue.worker.get_settings",
            return_value=_fake_settings(queue_high_concurrency=2, queue_low_concurrency=4),
        ):
            lanes = {lane.priority: lane for lane in get_queue_lanes()}

        assert lanes[JobPriority.HIGH].max_jobs == 2
        assert lanes[JobPriority.LOW].max_jobs == 4

    def test_concurrency_never_drops_below_one(self) -> None:
        """A zero/negative setting would silently stall that priority."""
        with patch(
            "app.services.queue.worker.get_settings",
            return_value=_fake_settings(queue_high_concurrency=0, queue_low_concurrency=-2),
        ):
            lanes = {lane.priority: lane for lane in get_queue_lanes()}

        assert lanes[JobPriority.HIGH].max_jobs == 1
        assert lanes[JobPriority.LOW].max_jobs == 1


class TestBuildWorker:
    """Worker wiring for a single lane."""

    def test_worker_consumes_its_lane_with_its_own_concurrency(self) -> None:
        worker = build_worker(
            QueueLane(JobPriority.HIGH, "test_queue:high", 2),
            WorkerSettings.configure(),
        )

        assert worker.queue_name == "test_queue:high"
        assert worker.max_jobs == 2

    def test_arq_signal_handlers_are_disabled(self) -> None:
        """arq's handlers replace each other; run_worker owns shutdown instead."""
        worker = build_worker(
            QueueLane(JobPriority.LOW, "test_queue:low", 1),
            WorkerSettings.configure(),
        )

        assert worker._handle_signals is False
