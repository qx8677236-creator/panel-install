"""计划任务、安全响应头、证书记录和 SSH 解析的纯逻辑测试。"""

import unittest
from pathlib import Path

from app.crontab.service import cron_expr, merge_crontab
from app.nginx.acme import record_order
from app.nginx.headers import normalize_security_headers, security_header_lines
from app.nginx.security import SiteError
from app.security.ssh import _findings


class AlignTest(unittest.TestCase):
    def test_cron_expr(self):
        self.assertEqual(cron_expr({"type": "minute", "where1": "5"}), "*/5 * * * *")
        self.assertEqual(cron_expr({"type": "day", "hour": "3", "minute": "30"}), "30 3 * * *")

    def test_merge_keeps_other_lines(self):
        merged = merge_crontab("0 1 * * * /bin/true\n", "# panel-crontab-begin\n15 4 * * * /bin/echo\n# panel-crontab-end\n")
        self.assertIn("/bin/true", merged)
        self.assertIn("/bin/echo", merged)
        cleared = merge_crontab(merged, "")
        self.assertIn("/bin/true", cleared)
        self.assertNotIn("panel-crontab", cleared)

    def test_headers_reject_injection(self):
        with self.assertRaises(SiteError):
            normalize_security_headers({"enabled": True, "x_frame_options": "SAMEORIGIN\nadd_header X 1;"})
        lines = security_header_lines({"enabled": True, "nosniff": True, "hsts": True}, False)
        self.assertIn('X-Content-Type-Options "nosniff"', lines[0])
        self.assertFalse(any("Strict-Transport" in line for line in lines))

    def test_ssh_findings(self):
        items = _findings({"permitrootlogin": "yes", "permitemptypasswords": "no"}, [])
        self.assertEqual(items[0]["item"], "PermitRootLogin")

    def test_acme_record_does_not_need_network(self):
        self.assertTrue(callable(record_order))
        source = Path(__file__).resolve().parents[1].joinpath("app/nginx/acme.py").read_text(encoding="utf-8")
        self.assertNotIn("certbot", source)


if __name__ == "__main__":
    unittest.main()
