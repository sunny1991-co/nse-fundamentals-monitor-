import unittest
from unittest.mock import Mock

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


if __name__ == '__main__':
    unittest.main()
