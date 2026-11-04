from django.core.management.base import BaseCommand
from django.conf import settings
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka import KafkaException, KafkaError


class Command(BaseCommand):
    def add_arguments(self, p):
        p.add_argument("--replication-factor", type=int, default=1)

    def handle(self, *args, **o):
        futures = AdminClient(settings.KAFKA).create_topics(
            [
                NewTopic(
                    t, num_partitions=6, replication_factor=o["replication_factor"]
                )
                for t in [settings.KAFKA_TOPIC, settings.KAFKA_DLQ]
            ]
        )
        for topic, f in futures.items():
            try:
                f.result()
            except KafkaException as exc:
                if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                    raise
            self.stdout.write(topic)
