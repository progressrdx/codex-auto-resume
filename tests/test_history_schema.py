"""Unrecognized App state must not fall back to a stale resumable turn."""
import copy
import unittest

from codex_resume.policy import decide, fingerprint, latest_turn, UnsupportedState
from test_resume import state


class HistorySchemaTests(unittest.TestCase):
    def assert_rejected(self, snapshot):
        self.assertEqual(decide(snapshot).action, 'stop')
        with self.assertRaises(UnsupportedState):
            latest_turn(snapshot)
        with self.assertRaises(UnsupportedState):
            fingerprint(snapshot)

    def test_unknown_history_cannot_authorize_using_legacy_turns(self):
        for history in ({'kind': 'future-format'}, {}, [], 'canonical', False):
            snapshot = state()
            snapshot['turns'] = [copy.deepcopy(latest_turn(snapshot))]
            snapshot['turnHistory'] = history
            with self.subTest(history=history):
                self.assert_rejected(snapshot)

    def test_malformed_canonical_history_stops_without_exception_retry(self):
        base = state()['turnHistory']['history']
        variants = [None, [], {}, {'islands': [None]}, {'islands': 'tail'},
                    dict(base, entitiesByKey=[]), dict(base, islands=[{
                        'entries': [{'value': []}], 'newerBoundary': {'status': 'exhausted'}}]),
                    dict(base, islands=[{'entries': [None], 'newerBoundary': {'status': 'exhausted'}}]),
                    dict(base, islands=[{'entries': 'entity', 'newerBoundary': {'status': 'exhausted'}}]),
                    dict(base, islands=[{'entries': [{'value': 'entity'}], 'newerBoundary': None}]),
                    dict(base, entitiesByKey={})]
        for history in variants:
            snapshot = state()
            snapshot['turnHistory']['history'] = history
            with self.subTest(history=history):
                self.assert_rejected(snapshot)

    def test_absent_or_null_history_still_supports_legacy_snapshots(self):
        for null in (False, True):
            snapshot = state()
            snapshot['turns'] = [copy.deepcopy(latest_turn(snapshot))]
            if null:
                snapshot['turnHistory'] = None
            else:
                del snapshot['turnHistory']
            self.assertEqual(decide(snapshot).action, 'resume')

    def test_controller_stops_without_quota_query_or_dispatch_intent(self):
        import tempfile
        from unittest.mock import Mock
        from codex_resume.controller import Controller
        from codex_resume.store import Store
        from test_resume import FakeDesktop, THREAD
        for history in ({'kind': 'future'}, {'kind': 'canonical', 'history': None}):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as directory:
                store = Store(directory)
                try:
                    store.arm(THREAD, 3)
                    snapshot = state()
                    snapshot['turns'] = [copy.deepcopy(latest_turn(snapshot))]
                    snapshot['turnHistory'] = history
                    desktop = FakeDesktop(snapshot)
                    quota_reader = Mock()
                    controller = Controller(THREAD, store, lambda: desktop, quota_reader)
                    self.assertFalse(controller.step())
                    self.assertEqual(store.get(THREAD)['status'], 'stopped')
                    self.assertFalse(store.get(THREAD)['enabled'])
                    self.assertEqual(store.count(THREAD), 0)
                    self.assertFalse(desktop.calls)
                    quota_reader.assert_not_called()
                finally:
                    store.close()
