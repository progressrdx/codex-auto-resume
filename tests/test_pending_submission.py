"""App-owned uncertain submissions must block our independent continuation."""
import tempfile
import unittest
from unittest.mock import Mock
from codex_resume.controller import Controller
from codex_resume.policy import decide, fingerprint
from codex_resume.store import Store
from test_resume import state, FakeDesktop, THREAD

class PendingSubmissionTests(unittest.TestCase):
    def test_pending_or_unknown_submission_state_stops_before_quota_or_intent(self):
        for pending in ([{'requestId':'pending'}], {}, 'unknown', False, 0):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as directory:
                snapshot=state();snapshot['unconfirmedTurnSubmissions']=pending
                self.assertEqual(decide(snapshot).action,'stop')
                store=Store(directory)
                try:
                    store.arm(THREAD,3);desktop=FakeDesktop(snapshot);quota=Mock()
                    self.assertFalse(Controller(THREAD,store,lambda:desktop,quota).step())
                    quota.assert_not_called();self.assertEqual(store.count(THREAD),0)
                    self.assertFalse(desktop.calls)
                finally:store.close()
    def test_absent_null_or_empty_submission_list_preserves_existing_behavior(self):
        for pending in (None,[]):
            snapshot=state();snapshot['unconfirmedTurnSubmissions']=pending
            self.assertEqual(decide(snapshot).action,'resume')
        self.assertEqual(decide(state()).action,'resume')
    def test_submission_changes_are_bound_to_fingerprint(self):
        snapshot=state();before=fingerprint(snapshot)
        snapshot['unconfirmedTurnSubmissions']=[{'requestId':'new'}]
        self.assertNotEqual(fingerprint(snapshot),before)
