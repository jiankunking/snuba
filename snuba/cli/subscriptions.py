import signal
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from typing import Optional, Sequence

import click

from snuba import settings
from snuba.datasets.factory import DATASET_NAMES, enforce_table_writer, get_dataset
from snuba.environment import setup_logging, setup_sentry
from snuba.redis import redis_client
from snuba.subscriptions.consumer import TickConsumer
from snuba.subscriptions.data import PartitionId
from snuba.subscriptions.executor import SubscriptionExecutor
from snuba.subscriptions.scheduler import SubscriptionScheduler
from snuba.subscriptions.store import RedisSubscriptionDataStore
from snuba.subscriptions.worker import (
    SubscriptionResultCodec,
    SubscriptionWorker,
)
from snuba.util import create_metrics
from snuba.utils.codecs import PassthroughCodec
from snuba.utils.streams.batching import BatchingConsumer
from snuba.utils.streams.kafka import (
    CommitCodec,
    KafkaConsumer,
    KafkaProducer,
    build_kafka_consumer_configuration,
)
from snuba.utils.streams.synchronized import SynchronizedConsumer
from snuba.utils.streams.types import Topic


@click.command()
@click.option(
    "--dataset",
    "dataset_name",
    default="events",
    type=click.Choice(DATASET_NAMES),
    help="The dataset to target",
)
@click.option("--topic")
@click.option("--partitions", type=int)
@click.option("--commit-log-topic")
@click.option(
    "--commit-log-group",
    "commit_log_groups",
    multiple=True,
    default=["snuba-consumers"],
)
@click.option(
    "--consumer-group",
    default="snuba-subscriptions",
    help="Consumer group use for consuming the dataset source topic.",
)
@click.option(
    "--auto-offset-reset",
    default="error",
    type=click.Choice(["error", "earliest", "latest"]),
    help="Kafka consumer auto offset reset.",
)
@click.option(
    "--bootstrap-server",
    "bootstrap_servers",
    multiple=True,
    help="Kafka bootstrap server(s) to use.",
)
@click.option(
    "--max-batch-size",
    default=settings.DEFAULT_MAX_BATCH_SIZE,
    type=int,
    help="Max number of messages to batch in memory before writing to Kafka.",
)
@click.option(
    "--max-batch-time-ms",
    default=settings.DEFAULT_MAX_BATCH_TIME_MS,
    type=int,
    help="Max length of time to buffer messages in memory before writing to Kafka.",
)
@click.option("--schedule-ttl", type=int, default=60 * 5)
@click.option("--result-topic")
@click.option("--log-level", help="Logging level to use.")
def subscriptions(
    *,
    dataset_name: str,
    topic: Optional[str],
    partitions: Optional[int],
    commit_log_topic: Optional[str],
    commit_log_groups: Sequence[str],
    consumer_group: str,
    auto_offset_reset: str,
    bootstrap_servers: Sequence[str],
    max_batch_size: int,
    max_batch_time_ms: int,
    schedule_ttl: int,
    result_topic: Optional[str],
    log_level: Optional[str],
) -> None:
    """Evaluates subscribed queries for a dataset."""

    assert result_topic is not None

    setup_logging(log_level)
    setup_sentry()

    dataset = get_dataset(dataset_name)

    if not bootstrap_servers:
        bootstrap_servers = settings.DEFAULT_DATASET_BROKERS.get(
            dataset_name, settings.DEFAULT_BROKERS
        )

    loader = enforce_table_writer(dataset).get_stream_loader()

    consumer = TickConsumer(
        SynchronizedConsumer(
            KafkaConsumer(
                build_kafka_consumer_configuration(
                    bootstrap_servers,
                    consumer_group,
                    auto_offset_reset=auto_offset_reset,
                ),
                PassthroughCodec(),
            ),
            KafkaConsumer(
                build_kafka_consumer_configuration(
                    bootstrap_servers,
                    f"subscriptions-commit-log-{uuid.uuid1().hex}",
                    auto_offset_reset="earliest",
                ),
                CommitCodec(),
            ),
            (
                Topic(commit_log_topic)
                if commit_log_topic is not None
                else Topic(loader.get_commit_log_topic_spec().topic_name)
            ),
            set(commit_log_groups),
        )
    )

    producer = KafkaProducer(
        {
            "bootstrap.servers": ",".join(bootstrap_servers),
            "partitioner": "consistent",
            "message.max.bytes": 50000000,  # 50MB, default is 1MB
        },
        SubscriptionResultCodec(),
    )

    with closing(consumer), closing(producer):
        batching_consumer = BatchingConsumer(
            consumer,
            (
                Topic(topic)
                if topic is not None
                else Topic(loader.get_default_topic_spec().topic_name)
            ),
            SubscriptionWorker(
                SubscriptionExecutor(
                    dataset,
                    ThreadPoolExecutor(
                        max_workers=settings.SUBSCRIPTIONS_MAX_CONCURRENT_QUERIES
                    ),
                ),
                {
                    index: SubscriptionScheduler(
                        RedisSubscriptionDataStore(
                            redis_client, dataset, PartitionId(index)
                        ),
                        PartitionId(index),
                        cache_ttl=timedelta(seconds=schedule_ttl),
                    )
                    for index in range(
                        partitions
                        if partitions is not None
                        else loader.get_default_topic_spec().partitions_number
                    )
                },
                producer,
                Topic(result_topic),
            ),
            max_batch_size,
            max_batch_time_ms,
            create_metrics(
                "snuba.subscriptions",
                tags={"group": consumer_group, "dataset": dataset_name},
            ),
        )

        def handler(signum, frame) -> None:
            batching_consumer.signal_shutdown()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

        batching_consumer.run()
