"""监听地址只根据已经给出的 ss 结果判断，不绑定 80 端口。"""

import unittest

from app.nginx.listen import decide_listen, parse_listeners
from app.nginx.security import SiteError


class ListenDecisionTests(unittest.TestCase):
    def test_loopback_only_uses_public_ipv4(self):
        listeners = parse_listeners(
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "LISTEN 0 511 127.0.0.1:8090 0.0.0.0:*\n"
        )
        self.assertEqual(decide_listen(8090, listeners, "10.34.0.4"), "10.34.0.4:8090")
        self.assertNotIn("0.0.0.0", decide_listen(8090, listeners, "10.34.0.4"))

    def test_wildcard_conflict_is_rejected_without_binding(self):
        listeners = [("0.0.0.0", 80), ("*", 80)]
        with self.assertRaises(SiteError) as caught:
            decide_listen(80, listeners, "10.34.0.4")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("80", caught.exception.message)
        self.assertIn("占用", caught.exception.message)

    def test_public_ip_conflict_is_rejected(self):
        listeners = [("10.34.0.4", 8090), ("127.0.0.1", 8090)]
        with self.assertRaises(SiteError) as caught:
            decide_listen(8090, listeners, "10.34.0.4")
        self.assertIn("8090", caught.exception.message)

    def test_own_public_listener_stays_on_that_ip(self):
        listeners = [("10.34.0.4", 8090), ("127.0.0.1", 8090)]
        chosen = decide_listen(8090, listeners, "10.34.0.4", own={("10.34.0.4", 8090)})
        self.assertEqual(chosen, "10.34.0.4:8090")
