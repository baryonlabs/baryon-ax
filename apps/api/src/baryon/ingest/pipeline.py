"""Broadway-style ingest pipeline in asyncio.

Elixir Broadway's value is not Elixir — it is a small set of contracts:

* producers pull from a source,
* consumers advertise demand, so producers cannot outrun them (backpressure),
* batchers group work by size *or* time,
* failures are retried and then dead-lettered instead of killing the pipeline,
* shutdown drains in-flight work.

This module implements those contracts for a single Python process. When the
work outgrows one process, replace the transport (NATS/Temporal) but keep the
stage contracts. See docs/ingestion/architecture.md §3.

Note: a batch is the unit of retry and dead-lettering. Put documents that must
not share a failure domain in separate batches (one document per batch, or
partition by `document_id`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterable, Awaitable, Callable, Generic, Iterable, TypeVar

T = TypeVar("T")

Handler = Callable[[list[T]], Awaitable[None]]

_SENTINEL = object()


@dataclass
class PipelineConfig:
    queue_maxsize: int = 32
    max_in_flight: int = 4
    batch_size: int = 32
    batch_timeout_s: float = 0.5
    max_retries: int = 3
    backoff_s: float = 0.05
    max_backoff_s: float = 5.0

    def __post_init__(self) -> None:
        if self.queue_maxsize < 1:
            raise ValueError("queue_maxsize must be >= 1")
        if self.max_in_flight < 1:
            raise ValueError("max_in_flight must be >= 1")
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if self.batch_timeout_s <= 0:
            raise ValueError("batch_timeout_s must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


class PermanentError(Exception):
    """Raised by a handler when retrying cannot help (bad input, 4xx).

    The pipeline dead-letters the batch immediately instead of burning retries.
    """


@dataclass
class DeadLetter:
    item: Any
    error: str
    attempts: int


@dataclass
class PipelineStats:
    produced: int = 0
    processed: int = 0
    batches: int = 0
    retries: int = 0
    dead_letters: list[DeadLetter] = field(default_factory=list)
    max_queue_depth: int = 0
    peak_in_flight: int = 0
    producer_error: str | None = None

    @property
    def failed(self) -> int:
        return len(self.dead_letters)


class Pipeline(Generic[T]):
    """Bounded, batched, retrying consumer.

    Example:
        pipeline = Pipeline(handler=index_batch, config=PipelineConfig(batch_size=16))
        stats = await pipeline.run(documents)
    """

    def __init__(self, handler: Handler[T], config: PipelineConfig | None = None) -> None:
        self._handler = handler
        self._config = config or PipelineConfig()
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._config.queue_maxsize)
        self._semaphore = asyncio.Semaphore(self._config.max_in_flight)
        self._stopping = asyncio.Event()
        self._in_flight = 0
        self._stats = PipelineStats()

    @property
    def stats(self) -> PipelineStats:
        return self._stats

    def stop(self) -> None:
        """Ask the producer to stop. In-flight batches still drain."""
        self._stopping.set()

    async def run(self, items: Iterable[T] | AsyncIterable[T]) -> PipelineStats:
        self._stats = PipelineStats()
        tasks: set[asyncio.Task[None]] = set()

        producer = asyncio.create_task(self._produce(items))

        try:
            while True:
                batch, sentinel = await self._next_batch()
                if batch:
                    await self._semaphore.acquire()
                    task = asyncio.create_task(self._consume(batch))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                if sentinel:
                    break
        finally:
            # Producer may have failed (e.g. source error); do not leak it.
            if not producer.done():
                producer.cancel()
            result = await asyncio.gather(producer, return_exceptions=True)
            for outcome in result:
                if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                    self._stats.producer_error = f"{type(outcome).__name__}: {outcome}"
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return self._stats

    async def _produce(self, items: Iterable[T] | AsyncIterable[T]) -> None:
        try:
            async for item in _aiter(items):
                if self._stopping.is_set():
                    break
                # Blocks when the queue is full; this is the backpressure.
                await self._queue.put(item)
                self._stats.produced += 1
                depth = self._queue.qsize()
                if depth > self._stats.max_queue_depth:
                    self._stats.max_queue_depth = depth
        finally:
            await self._queue.put(_SENTINEL)

    async def _next_batch(self) -> tuple[list[T], bool]:
        first = await self._queue.get()
        if first is _SENTINEL:
            return [], True

        batch: list[T] = [first]
        sentinel = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.batch_timeout_s

        while len(batch) < self._config.batch_size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if item is _SENTINEL:
                sentinel = True
                break
            batch.append(item)
        return batch, sentinel

    async def _consume(self, batch: list[T]) -> None:
        self._in_flight += 1
        if self._in_flight > self._stats.peak_in_flight:
            self._stats.peak_in_flight = self._in_flight
        attempt = 0
        try:
            while True:
                try:
                    await self._handler(batch)
                    self._stats.processed += len(batch)
                    self._stats.batches += 1
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - batch boundary
                    attempt += 1
                    permanent = isinstance(error, PermanentError)
                    if permanent or attempt > self._config.max_retries:
                        for item in batch:
                            self._stats.dead_letters.append(
                                DeadLetter(item=item, error=f"{type(error).__name__}: {error}", attempts=attempt)
                            )
                        return
                    self._stats.retries += 1
                    delay = min(
                        self._config.backoff_s * (2 ** (attempt - 1)),
                        self._config.max_backoff_s,
                    )
                    await asyncio.sleep(delay)
        finally:
            self._in_flight -= 1
            self._semaphore.release()


async def _aiter(items: Iterable[T] | AsyncIterable[T]):
    if hasattr(items, "__aiter__"):
        async for item in items:  # type: ignore[union-attr]
            yield item
        return
    for item in items:  # type: ignore[union-attr]
        yield item


async def run_pipeline(
    items: Iterable[T] | AsyncIterable[T],
    handler: Handler[T],
    config: PipelineConfig | None = None,
) -> PipelineStats:
    return await Pipeline(handler, config).run(items)
