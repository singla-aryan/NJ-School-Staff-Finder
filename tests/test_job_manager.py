import threading
import time

from src.job_manager import SchoolJobManager


def test_new_school_can_be_added_without_canceling_active_school():
    manager = SchoolJobManager(max_workers=1)
    first_started = threading.Event()
    release_first = threading.Event()

    def first_task(report):
        report("crawling", {"pages": 4})
        first_started.set()
        assert release_first.wait(timeout=2)
        return "first-result"

    def second_task(report):
        report("crawling", {"pages": 1})
        return "second-result"

    try:
        assert manager.submit("First School", first_task)
        assert first_started.wait(timeout=2)
        assert manager.submit("Second School", second_task)
        assert manager.has_active()

        _completed, snapshots = manager.poll()
        states = {snapshot.name: snapshot.state for snapshot in snapshots}
        assert states["First School"] == "running"
        assert states["Second School"] == "queued"

        release_first.set()
        results = {}
        deadline = time.monotonic() + 3
        while len(results) < 2 and time.monotonic() < deadline:
            completed, _snapshots = manager.poll()
            results.update({job.name: job.result for job in completed})
            time.sleep(0.01)
        assert results == {
            "First School": "first-result",
            "Second School": "second-result",
        }
        assert not manager.has_active()
    finally:
        release_first.set()
        manager.shutdown()


def test_duplicate_school_is_not_submitted_twice():
    manager = SchoolJobManager(max_workers=1)
    release = threading.Event()

    def task(_report):
        release.wait(timeout=2)
        return "done"

    try:
        assert manager.submit("Same School", task)
        assert not manager.submit("Same School", task)
    finally:
        release.set()
        manager.shutdown()
