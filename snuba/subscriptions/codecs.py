import json
from datetime import timedelta

from snuba.subscriptions.data import SubscriptionData
from snuba.utils.codecs import Codec


class SubscriptionDataCodec(Codec[bytes, SubscriptionData]):
    def encode(self, value: SubscriptionData) -> bytes:
        return json.dumps(
            {
                "project_id": value.project_id,
                "conditions": value.conditions,
                "aggregations": value.aggregations,
                "time_window": int(value.time_window.total_seconds()),
                "resolution": int(value.resolution.total_seconds()),
            }
        ).encode("utf-8")

    def decode(self, value: bytes) -> SubscriptionData:
        data = json.loads(value.decode("utf-8"))
        return SubscriptionData(
            project_id=data["project_id"],
            conditions=data["conditions"],
            aggregations=data["aggregations"],
            time_window=timedelta(seconds=data["time_window"]),
            resolution=timedelta(seconds=data["resolution"]),
        )
