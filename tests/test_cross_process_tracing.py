def test_cross_process_trace_context_creates_real_otel_parent():
    from antisentinel.tracing.telemetry import Telemetry, TraceContext

    producer = Telemetry()
    with producer.span("producer") as parent:
        carrier = TraceContext.from_otel(parent).inject()

    consumer = Telemetry()
    with consumer.span("consumer", context=TraceContext.extract(carrier)):
        pass

    producer_span = next(span for span in producer.finished_spans if span.name == "producer")
    consumer_span = next(span for span in consumer.finished_spans if span.name == "consumer")
    assert consumer_span.context.trace_id == producer_span.context.trace_id
    assert consumer_span.parent is not None
    assert consumer_span.parent.span_id == producer_span.context.span_id


def test_sqlite_exporter_uses_standard_parent_span_id(tmp_path):
    import json
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    from antisentinel.tracing.telemetry import Telemetry, TraceContext

    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    telemetry = Telemetry(database=database)
    producer = Telemetry()
    with producer.span("producer") as parent:
        carrier = TraceContext.from_otel(parent).inject()
    with telemetry.span("consumer", context=TraceContext.extract(carrier), parent_request_id="request-alias"):
        pass

    row = database.query("SELECT trace_id, span_id, parent_span_id, attributes_json FROM spans WHERE name='consumer'")[0]
    producer_span = next(span for span in producer.finished_spans if span.name == "producer")
    assert row["trace_id"] == str(producer_span.context.trace_id)
    assert row["parent_span_id"] == str(producer_span.context.span_id)
    assert row["parent_span_id"] != "request-alias"
    assert "request_alias" not in json.loads(row["attributes_json"])
