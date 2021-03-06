import calendar
from datetime import datetime
from functools import partial
import simplejson as json
import uuid

from snuba import settings
from snuba.datasets.factory import enforce_table_writer, get_dataset

from tests.base import BaseApiTest, get_event


class TestDiscoverApi(BaseApiTest):
    def setup_method(self, test_method):
        # Setup both tables
        super().setup_method(test_method, "events")
        super().setup_method(test_method, "transactions")

        self.app.post = partial(self.app.post, headers={"referer": "test"})
        self.project_id = 1

        self.base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        self.generate_event()
        self.generate_transaction()

    def generate_event(self):
        self.dataset = get_dataset("events")
        event = get_event()
        event["project_id"] = self.project_id
        event = (
            enforce_table_writer(self.dataset)
            .get_stream_loader()
            .get_processor()
            .process_insert(event)
        )
        self.write_processed_records([event])

    def generate_transaction(self):
        self.dataset = get_dataset("transactions")

        trace_id = "7400045b25c443b885914600aa83ad04"
        span_id = "8841662216cc598b"
        processed = (
            enforce_table_writer(self.dataset)
            .get_stream_loader()
            .get_processor()
            .process_message(
                (
                    2,
                    "insert",
                    {
                        "project_id": self.project_id,
                        "event_id": uuid.uuid4().hex,
                        "deleted": 0,
                        "datetime": (self.base_time).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "platform": "python",
                        "retention_days": settings.DEFAULT_RETENTION_DAYS,
                        "data": {
                            "received": calendar.timegm((self.base_time).timetuple()),
                            "type": "transaction",
                            "transaction": "/api/do_things",
                            "start_timestamp": datetime.timestamp(self.base_time),
                            "timestamp": datetime.timestamp(self.base_time),
                            "tags": {
                                # Sentry
                                "environment": u"prød",
                                "sentry:release": "1",
                                "sentry:dist": "dist1",
                                # User
                                "foo": "baz",
                                "foo.bar": "qux",
                                "os_name": "linux",
                            },
                            "user": {
                                "email": "sally@example.org",
                                "ip_address": "8.8.8.8",
                                "geo": {
                                    "city": "San Francisco",
                                    "region": "CA",
                                    "country_code": "US",
                                },
                            },
                            "contexts": {
                                "trace": {
                                    "trace_id": trace_id,
                                    "span_id": span_id,
                                    "op": "http",
                                },
                            },
                            "sdk": {
                                "name": "sentry.python",
                                "version": "0.13.4",
                                "integrations": ["django"],
                            },
                            "spans": [
                                {
                                    "op": "db",
                                    "trace_id": trace_id,
                                    "span_id": span_id + "1",
                                    "parent_span_id": None,
                                    "same_process_as_parent": True,
                                    "description": "SELECT * FROM users",
                                    "data": {},
                                    "timestamp": calendar.timegm(
                                        (self.base_time).timetuple()
                                    ),
                                }
                            ],
                        },
                    },
                )
            )
        )

        self.write_processed_events(processed.data)

    def test_raw_data(self):
        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "selected_columns": ["type", "tags[custom_tag]", "release"],
                    "conditions": [["type", "!=", "transaction"]],
                    "orderby": "timestamp",
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert len(data["data"]) == 1, data
        assert data["data"][0] == {
            "type": "error",
            "tags[custom_tag]": "custom_value",
            "release": None,
        }

        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": 1,
                    "selected_columns": [
                        "type",
                        "tags[foo]",
                        "group_id",
                        "release",
                        "sdk_name",
                        "geo_city",
                    ],
                    "conditions": [["type", "=", "transaction"]],
                    "orderby": "timestamp",
                    "limit": 1,
                }
            ),
        )
        data = json.loads(response.data)
        assert response.status_code == 200
        assert len(data["data"]) == 1, data
        assert data["data"][0] == {
            "type": "transaction",
            "tags[foo]": "baz",
            "group_id": 0,
            "release": "1",
            "geo_city": "San Francisco",
            "sdk_name": "sentry.python",
        }

    def test_aggregations(self):
        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["count()", None, "count"]],
                    "groupby": ["project_id", "tags[custom_tag]"],
                    "conditions": [["type", "!=", "transaction"]],
                    "orderby": "count",
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [
            {"count": 1, "tags[custom_tag]": "custom_value", "project_id": 1}
        ]

        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["count()", "", "count"]],
                    "groupby": ["project_id", "tags[foo]"],
                    "conditions": [["type", "=", "transaction"]],
                    "orderby": "count",
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [{"count": 1, "tags[foo]": "baz", "project_id": 1}]

    def test_handles_columns_from_other_dataset(self):
        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [
                        ["count()", "", "count"],
                        ["uniq", ["group_id"], "uniq_group_id"],
                        ["uniq", ["exception_stacks.type"], "uniq_ex_stacks"],
                    ],
                    "conditions": [["type", "=", "transaction"], ["group_id", "=", 2]],
                    "groupby": ["type"],
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [
            {"type": "transaction", "count": 0, "uniq_group_id": 0, "uniq_ex_stacks": 0}
        ]

        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["uniq", ["trace_id"], "uniq_trace_id"]],
                    "conditions": [["type", "!=", "transaction"]],
                    "groupby": "type",
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [{"type": "error", "uniq_trace_id": 0}]

    def test_geo_column_condition(self):
        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["count()", "", "count"]],
                    "conditions": [
                        ["type", "=", "transaction"],
                        ["geo_country_code", "=", "MX"],
                    ],
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [{"count": 0}]

        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["count()", "", "count"]],
                    "conditions": [
                        ["type", "=", "transaction"],
                        ["geo_country_code", "=", "US"],
                        ["geo_region", "=", "CA"],
                        ["geo_city", "=", "San Francisco"],
                    ],
                    "limit": 1000,
                }
            ),
        )
        data = json.loads(response.data)

        assert response.status_code == 200
        assert data["data"] == [{"count": 1}]

    def test_exception_stack_column_condition(self):
        response = self.app.post(
            "/query",
            data=json.dumps(
                {
                    "dataset": "discover",
                    "project": self.project_id,
                    "aggregations": [["count()", "", "count"]],
                    "conditions": [
                        ["exception_stacks.type", "LIKE", "Arithmetic%"],
                        ["exception_frames.filename", "LIKE", "%.java"],
                    ],
                    "limit": 1000,
                }
            ),
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"] == [{"count": 1}]

    def test_having(self):
        result = json.loads(
            self.app.post(
                "/query",
                data=json.dumps(
                    {
                        "dataset": "discover",
                        "project": self.project_id,
                        "groupby": "primary_hash",
                        "conditions": [["type", "!=", "transaction"]],
                        "having": [["times_seen", "=", 1]],
                        "aggregations": [["count()", "", "times_seen"]],
                    }
                ),
            ).data
        )
        assert len(result["data"]) == 1

    def test_time(self):
        result = json.loads(
            self.app.post(
                "/query",
                data=json.dumps(
                    {
                        "dataset": "discover",
                        "project": self.project_id,
                        "selected_columns": ["project_id"],
                        "groupby": ["time", "project_id"],
                        "conditions": [["type", "!=", "transaction"]],
                    }
                ),
            ).data
        )
        assert len(result["data"]) == 1

        result = json.loads(
            self.app.post(
                "/query",
                data=json.dumps(
                    {
                        "dataset": "discover",
                        "project": self.project_id,
                        "selected_columns": ["project_id"],
                        "groupby": ["time", "project_id"],
                        "conditions": [["type", "=", "transaction"]],
                    }
                ),
            ).data
        )
        assert len(result["data"]) == 1

    def test_transaction_group_ids(self):
        result = json.loads(
            self.app.post(
                "/query",
                data=json.dumps(
                    {
                        "dataset": "discover",
                        "project": self.project_id,
                        "selected_columns": ["group_id"],
                        "conditions": [["type", "=", "transaction"]],
                    }
                ),
            ).data
        )
        assert result["data"][0]["group_id"] == 0

        result = json.loads(
            self.app.post(
                "/query",
                data=json.dumps(
                    {
                        "dataset": "discover",
                        "project": self.project_id,
                        "selected_columns": ["group_id"],
                        "conditions": [
                            ["type", "=", "transaction"],
                            ["group_id", "IN", (1, 2, 3, 4)],
                        ],
                    }
                ),
            ).data
        )

        assert result["data"] == []
