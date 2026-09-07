from datetime import datetime, timezone


class FakeConfig:
    poll_seconds = 0.001


class FakeScheduler:
    def __init__(self):
        self.calls = 0

    def tick(self, now):
        self.calls += 1


class FakeWorker:
    def __init__(self):
        self.calls = 0

    def run_once(self, now=None):
        self.calls += 1


def test_daemon_stop_breaks_poll_loop_without_losing_durable_components():
    from antisentinel.code_map.daemon import CodeMapDaemon

    scheduler = FakeScheduler()
    worker = FakeWorker()
    daemon = CodeMapDaemon(FakeConfig(), scheduler, worker)
    daemon.stop()
    daemon.run_forever()

    assert scheduler.calls == 0
    assert worker.calls == 0
    assert daemon.health()["status"] == "stopping"


def test_daemon_health_counts_scheduler_and_worker_iterations():
    from antisentinel.code_map.daemon import CodeMapDaemon

    scheduler = FakeScheduler()
    worker = FakeWorker()
    daemon = CodeMapDaemon(FakeConfig(), scheduler, worker)

    import threading
    thread = threading.Thread(target=daemon.run_forever)
    thread.start()
    while scheduler.calls < 2:
        pass
    daemon.stop()
    thread.join(timeout=1)

    assert daemon.health()["ticks"] >= 2
    assert daemon.health()["worker_runs"] == daemon.health()["ticks"]


def test_daemon_emits_scheduler_and_worker_trace_spans():
    from antisentinel.code_map.daemon import CodeMapDaemon
    from antisentinel.tracing.telemetry import Telemetry

    scheduler = FakeScheduler()
    worker = FakeWorker()
    telemetry = Telemetry()
    daemon = CodeMapDaemon(FakeConfig(), scheduler, worker, telemetry=telemetry)

    import threading
    thread = threading.Thread(target=daemon.run_forever)
    thread.start()
    while scheduler.calls < 1:
        pass
    daemon.stop()
    thread.join(timeout=1)

    spans = {span.name: span for span in telemetry.finished_spans}
    assert set(spans) >= {"code_map.tick", "code_map.scheduler.tick", "code_map.worker.run_once"}
    assert spans["code_map.scheduler.tick"].context.trace_id == spans["code_map.worker.run_once"].context.trace_id
    assert spans["code_map.scheduler.tick"].parent.span_id == spans["code_map.tick"].context.span_id
    assert spans["code_map.worker.run_once"].parent.span_id == spans["code_map.tick"].context.span_id
