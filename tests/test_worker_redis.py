import time

from antisentinel.memory.candidates import CandidateSource, MemoryCandidate
from antisentinel.memory.jobs import MemoryJob


def test_redis_cache_round_trips_versioned_value_with_fake_client():
    from antisentinel.adapters.cache.redis import RedisMemoryCache

    class FakeRedis:
        def __init__(self):
            self.values = {}

        def setex(self, key, ttl, value):
            self.values[key] = value

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            self.values.pop(key, None)

    cache = RedisMemoryCache(FakeRedis())
    cache.set("memory:key", {"ok": True}, ttl_seconds=60, version=3)

    assert cache.get("memory:key").value == {"ok": True}
    assert cache.get("memory:key", version=2) is None
    cache.delete("memory:key")
    assert cache.get("memory:key") is None


def test_redis_cache_and_index_apply_namespace_to_every_key():
    from antisentinel.adapters.cache.redis import RedisMemoryCache

    class FakeRedis:
        def __init__(self):
            self.values, self.hashes, self.sorted, self.expired = {}, {}, {}, []

        def setex(self, key, ttl, value): self.values[key] = value
        def get(self, key): return self.values.get(key)
        def hset(self, key, mapping): self.hashes.setdefault(key, {}).update(mapping)
        def zadd(self, key, mapping): self.sorted.setdefault(key, {}).update(mapping)
        def expire(self, key, ttl): self.expired.append(key)

    client = FakeRedis()
    cache = RedisMemoryCache(client, namespace="case:one")
    cache.set("rollout:session-1", {"ok": True}, ttl_seconds=60, version=1)
    cache.index.put("memory:rollouts:index", "memory:rollout", "session-1", 1, {"summary": "done"}, ttl_seconds=60)

    assert set(client.values) == {"case:one:rollout:session-1"}
    assert set(client.sorted) == {"case:one:memory:rollouts:index"}
    assert set(client.hashes) == {"case:one:memory:rollout:session-1"}


def test_redis_memory_index_links_zset_member_to_hash_content():
    from antisentinel.adapters.cache.redis import RedisMemoryIndex

    class FakeRedis:
        def __init__(self):
            self.hashes = {}
            self.sorted = {}

        def hset(self, key, mapping):
            self.hashes.setdefault(key, {}).update(mapping)

        def hgetall(self, key):
            return self.hashes.get(key, {})

        def zadd(self, key, mapping):
            self.sorted.setdefault(key, {}).update(mapping)

        def zrevrange(self, key, start, stop):
            values = sorted(self.sorted.get(key, {}).items(), key=lambda item: item[1], reverse=True)
            return [item[0] for item in values[start:stop + 1]]

        def expire(self, key, ttl):
            return True

    client = FakeRedis()
    index = RedisMemoryIndex(client)
    index.put("memory:rollouts:index", "memory:rollout", "rollout-1", 100, {"summary": "fixed"}, ttl_seconds=60)

    assert index.recent("memory:rollouts:index", "memory:rollout", limit=10) == [{"summary": "fixed", "memory_id": "rollout-1"}]


def test_memory_worker_consumes_job_without_blocking_runtime(tmp_path):
    from antisentinel.memory.recorder import MemoryRecorder
    from antisentinel.memory.worker import MemoryWorker
    from antisentinel.ports.model import FinalDiagnosis
    from antisentinel.worker.runtime.loop import RuntimeResult

    result = RuntimeResult(
        status="completed", incident_id="incident-worker", session_id="session-worker", turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="upstream timeout", confidence=0.9, evidence_refs=()),
        evidence_refs=(), task_summaries=(), error=None, events=(),
    )
    recorder = MemoryRecorder(tmp_path)
    recorder.record(result, operator_id="operator-1")
    worker = MemoryWorker(recorder, poll_interval=0.001)
    worker.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and recorder.memory_jobs._pending:
        time.sleep(0.01)
    drained = worker.stop()

    assert worker.processed_jobs >= 1
    assert drained is True


class FakeQueueRedis:
    def __init__(self):
        self.hashes = {}
        self.sorted = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def zadd(self, key, mapping, nx=False):
        target = self.sorted.setdefault(key, {})
        for member, score in mapping.items():
            if not nx or member not in target:
                target[member] = score

    def zrem(self, key, member):
        self.sorted.get(key, {}).pop(member, None)

    def delete(self, key):
        self.hashes.pop(key, None)

    def eval(self, script, key_count, pending, processing, job_prefix, now, lease_until):
        expired = sorted((score, member) for member, score in self.sorted.get(processing, {}).items() if score <= float(now))
        if expired:
            member = expired[0][1]
            self.zrem(processing, member)
            self.zadd(pending, {member: float(now)})
        ready = sorted((score, member) for member, score in self.sorted.get(pending, {}).items() if score <= float(now))
        if not ready:
            return None
        member = ready[0][1]
        self.zrem(pending, member)
        self.zadd(processing, {member: float(lease_until)})
        return self.hget(job_prefix + member, "payload")


def test_redis_memory_job_queue_recovers_expired_lease_and_acks():
    from antisentinel.memory.jobs import RedisMemoryJobQueue

    client = FakeQueueRedis()
    now = [100.0]
    queue = RedisMemoryJobQueue(client, "case:memory", lease_seconds=30, clock=lambda: now[0])
    source = CandidateSource(operator_id="operator-1", session_id="session-1", user_input="先看日志")
    job = MemoryJob(
        job_id="job-1",
        source=source,
        candidates=(MemoryCandidate("candidate-1", "operator-1", "session-1", "logs_before_metrics", "user_input"),),
    )

    queue.enqueue(job)
    assert queue.claim() == job
    client.sorted["case:memory:processing"]["job-1"] = 99.0
    assert queue.claim() == job
    queue.retry("job-1", "classifier timeout")
    now[0] = 101.0
    retried = queue.claim()
    assert retried.attempts == 1
    queue.ack("job-1")
    assert queue.claim() is None


def test_redis_cache_lookup_distinguishes_miss_and_bypass():
    from antisentinel.adapters.cache.redis import RedisMemoryCache

    class Missing:
        def get(self, key):
            return None

    class Broken:
        def get(self, key):
            raise ConnectionError("redis unavailable")

    assert RedisMemoryCache(Missing()).lookup("memory:key").status == "miss"
    assert RedisMemoryCache(Broken()).lookup("memory:key").status == "bypass"
