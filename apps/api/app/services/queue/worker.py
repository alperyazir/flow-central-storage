"""Worker runner configuration for arq.

Jobs are routed to a queue per priority (``<queue_name>:high|normal|low``, see
``QueueService.enqueue_job``). An arq Worker consumes exactly one queue, so this
process runs one worker per priority — otherwise jobs enqueued outside the
consumed queue would sit in Redis forever.

Each lane has its own concurrency budget rather than sharing one, so a high
priority job gets a free slot immediately instead of queueing behind a bulk
sweep, and a bulk sweep on the low lane can never starve everyday work.
"""

import asyncio
import logging
import signal
import sys
from typing import Any, NamedTuple

from arq.connections import RedisSettings
from arq.worker import Worker

from app.core.config import get_settings
from app.services.queue.models import JobPriority
from app.services.queue.tasks import (
    create_bundle_task,
    on_job_end,
    on_job_start,
    process_book_task,
    process_material_task,
)

logger = logging.getLogger(__name__)


def _get_retry_delay(attempt: int) -> float:
    """Calculate retry delay with exponential backoff.

    Args:
        attempt: Current attempt number (0-indexed)

    Returns:
        Delay in seconds
    """
    settings = get_settings()
    base_delay = settings.queue_retry_delay_seconds
    # Exponential backoff: base * 2^attempt, capped at 1 hour
    return min(base_delay * (2**attempt), 3600)


class WorkerSettings:
    """arq worker configuration.

    This class defines the worker settings used by arq to configure
    the background worker process.
    """

    # Task functions to register
    functions = [process_book_task, process_material_task, create_bundle_task]

    # Lifecycle hooks
    on_startup = on_job_start
    on_shutdown = on_job_end

    # Redis connection settings (set dynamically)
    redis_settings: RedisSettings = None  # type: ignore

    # Concurrency and timeout settings (set dynamically)
    max_jobs: int = 3
    job_timeout: int = 3600
    max_tries: int = 3
    health_check_interval: int = 30

    # Retry configuration
    retry_jobs: bool = True

    @staticmethod
    def get_retry_delay(attempt: int) -> float:
        """Get retry delay for attempt."""
        return _get_retry_delay(attempt)

    # Queue name (set dynamically)
    queue_name: str = "arq:queue"

    @classmethod
    def configure(cls) -> type["WorkerSettings"]:
        """Configure settings from environment.

        Returns:
            Configured WorkerSettings class
        """
        settings = get_settings()

        cls.redis_settings = RedisSettings.from_dsn(settings.redis_url)
        cls.max_jobs = settings.queue_max_concurrency
        cls.job_timeout = settings.queue_job_timeout_seconds
        cls.max_tries = settings.queue_max_retries
        # The everyday lane; the other priorities get their own workers.
        cls.queue_name = f"{settings.queue_name}:{JobPriority.NORMAL.value}"

        return cls


class QueueLane(NamedTuple):
    """One arq queue consumed by this process."""

    priority: JobPriority
    queue_name: str
    max_jobs: int


def get_queue_lanes() -> list[QueueLane]:
    """Return every priority queue this process must consume.

    Must stay in sync with the queue names QueueService.enqueue_job writes to —
    a priority missing here means those jobs are enqueued and never run.
    """
    settings = get_settings()
    concurrency = {
        JobPriority.HIGH: settings.queue_high_concurrency,
        JobPriority.NORMAL: settings.queue_max_concurrency,
        JobPriority.LOW: settings.queue_low_concurrency,
    }

    return [
        QueueLane(
            priority=priority,
            queue_name=f"{settings.queue_name}:{priority.value}",
            max_jobs=max(1, concurrency[priority]),
        )
        for priority in JobPriority
    ]


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup hook.

    Args:
        ctx: arq context
    """
    logger.info("Worker starting up")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown hook.

    Args:
        ctx: arq context
    """
    logger.info("Worker shutting down")


def build_worker(lane: QueueLane, settings_cls: type["WorkerSettings"]) -> Worker:
    """Create the arq worker consuming a single priority lane.

    ``handle_signals=False`` is required: arq installs its handlers with
    ``loop.add_signal_handler``, which replaces the previous one, so with
    several workers only the last would ever see SIGTERM. run_worker() installs
    a single handler that stops every lane instead.
    """
    return Worker(
        functions=settings_cls.functions,
        redis_settings=settings_cls.redis_settings,
        max_jobs=lane.max_jobs,
        job_timeout=settings_cls.job_timeout,
        max_tries=settings_cls.max_tries,
        health_check_interval=settings_cls.health_check_interval,
        retry_jobs=settings_cls.retry_jobs,
        queue_name=lane.queue_name,
        on_startup=startup,
        on_shutdown=shutdown,
        handle_signals=False,
    )


def _install_shutdown_handler(workers: list[Worker]) -> None:
    """Stop every lane on SIGINT/SIGTERM.

    Mirrors arq's own stock handler (cancel in-flight jobs, then the poll
    loop); with ``retry_jobs`` on, cancelled jobs are retried on the next run.
    """

    def _handle(signum: int) -> None:
        logger.info("Shutdown signal %s received, stopping %d lanes", signum, len(workers))
        for worker in workers:
            worker.handle_sig(signum)  # type: ignore[arg-type]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle, sig)
        except NotImplementedError:  # pragma: no cover - non-unix
            signal.signal(sig, lambda signum, _frame: _handle(signum))


async def run_worker() -> None:
    """Run one arq worker per priority queue until they stop.

    The lanes run concurrently in this process. If one of them dies the rest
    are torn down too, so the container exits and the orchestrator restarts the
    whole set — a half-dead worker would silently stop draining a priority.
    """
    settings_cls = WorkerSettings.configure()
    lanes = get_queue_lanes()

    logger.info(
        "Starting workers for %s with job_timeout=%d, max_tries=%d",
        ", ".join(f"{lane.queue_name} (max_jobs={lane.max_jobs})" for lane in lanes),
        settings_cls.job_timeout,
        settings_cls.max_tries,
    )

    workers = [build_worker(lane, settings_cls) for lane in lanes]
    _install_shutdown_handler(workers)

    tasks = [asyncio.create_task(worker.async_run()) for worker in workers]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Worker lanes cancelled")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for worker in workers:
            try:
                await worker.close()
            except asyncio.CancelledError:
                # close() gathers the in-flight jobs it just cancelled.
                pass
        logger.info("Workers stopped")


def main() -> None:
    """Entry point for running the worker from command line.

    Usage:
        python -m app.services.queue.worker
    """
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Initializing AI processing queue worker")

    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
        sys.exit(0)
    except Exception as e:
        logger.error("Worker failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
