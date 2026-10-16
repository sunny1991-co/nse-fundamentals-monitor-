import unittest
from unittest.mock import Mock, patch

import termux_supervisor as supervisor


class TermuxSupervisorTests(unittest.TestCase):
    def test_restart_delay_uses_short_delay_until_restart_storm(self):
        restarts = []
        for second in [0, 30, 60, 90, 120]:
            restarts, delay = supervisor.restart_delay(restarts, second)
            self.assertEqual(delay, 5)
        restarts, delay = supervisor.restart_delay(restarts, 150)
        self.assertEqual(delay, 60)

    def test_restart_delay_discards_old_attempts(self):
        restarts, delay = supervisor.restart_delay([0, 10, 20, 30, 40, 50], 400)
        self.assertEqual(restarts, [400])
        self.assertEqual(delay, 5)

    def test_wakelock_absent_is_nonfatal(self):
        run = Mock()
        self.assertFalse(supervisor.acquire_wakelock(run=run, which=lambda _name: None))
        run.assert_not_called()

    def test_wakelock_failure_is_visible_as_false(self):
        result = Mock(returncode=1)
        run = Mock(return_value=result)
        self.assertFalse(supervisor.acquire_wakelock(run=run, which=lambda _name: '/bin/fake'))
        run.assert_called_once()

    def test_terminate_all_signals_every_child_before_waiting(self):
        events = []

        class Child:
            def __init__(self, pid):
                self.pid = pid
                self.done = False

            def poll(self):
                return 0 if self.done else None

            def wait(self, timeout=None):
                events.append(('wait', self.pid))
                self.done = True
                return 0

        children = {'a': Child(101), 'b': Child(202)}
        with patch.object(
            supervisor.os,
            'killpg',
            side_effect=lambda pid, sig: events.append(('signal', pid)),
        ):
            supervisor.terminate_all(children)

        self.assertEqual(events[:2], [('signal', 101), ('signal', 202)])
        self.assertEqual(events[2:], [('wait', 101), ('wait', 202)])

    def test_history_worker_is_separate_supervised_process(self):
        command = supervisor.history_command()
        self.assertTrue(command[-1].endswith('termux_history_worker.py'))

    def test_research_worker_is_separate_supervised_process(self):
        command = supervisor.research_command()
        self.assertTrue(command[-1].endswith('termux_research_worker.py'))

    def test_dashboard_requires_complete_private_enrollment(self):
        self.assertEqual(supervisor.dashboard_mode({}), 'NOT_ENROLLED')
        self.assertEqual(supervisor.dashboard_mode({'APP_SECRET_KEY':'x'}), 'CONFIG_INCOMPLETE')
        env={key:'value' for key in supervisor.AUTH_KEYS}
        self.assertEqual(supervisor.dashboard_mode(env), 'LOOPBACK_MFA_ENABLED')
        command=supervisor.dashboard_command(env)
        self.assertIn('127.0.0.1:8787', command)
        local=supervisor.dashboard_env(env)
        self.assertEqual(local['LOCAL_LOOPBACK_ONLY'],'1')
        self.assertEqual(local['APP_HOST'],'localhost')
        self.assertEqual(local['SMS_ENABLED'],'0')


if __name__ == '__main__':
    unittest.main()
