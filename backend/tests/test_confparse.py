import unittest

from app.nginx.confparse import parse_site_files

LIVE = """
server {
    listen 9999;
    server_name 20.187.70.213;
    root "/var/www/panel/20.187.70.213";
    index index.html index.htm;
    include "/tmp/rewrite.conf";
    # panel-proxy zhuanquan
    location / {
        proxy_pass http://18.166.2.175:14885;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
"""


class ConfParseTests(unittest.TestCase):
    def test_live_proxy_and_empty_rewrite(self):
        parsed = parse_site_files(
            LIVE,
            primary="20.187.70.213",
            primary_root="/var/www/panel/20.187.70.213",
            rewrite_text="# 根路径已交给反向代理。\n",
            file_root="/var/www/panel",
        )
        self.assertEqual(parsed["domains"], [{"domain": "20.187.70.213", "port": 9999}])
        self.assertEqual(parsed["proxies"][0]["target_url"], "http://18.166.2.175:14885")
        self.assertEqual(parsed["proxies"][0]["name"], "zhuanquan")
        self.assertEqual(parsed["rewrite_body"], "")
        self.assertEqual(parsed["preset"], "")
        self.assertEqual(parsed["limit"], {"conn": 0, "rate": 0})
        self.assertFalse(parsed["ssl"])
        self.assertFalse(parsed["auth"]["enabled"])
        self.assertEqual(parsed["auth"]["realm"], "")
        self.assertEqual(parsed["index_files"], "index.html index.htm")
        self.assertEqual(parsed["root"], "20.187.70.213")

    def test_missing_directives_stay_empty(self):
        parsed = parse_site_files("", primary="example.com", primary_root="/var/www/panel/example.com")
        self.assertEqual(parsed["domains"], [])
        self.assertEqual(parsed["proxies"], [])
        self.assertEqual(parsed["bindings"], [])
        self.assertEqual(parsed["redirects"], [])
        self.assertEqual(parsed["rewrite_body"], "")
        self.assertEqual(parsed["limit"], {"conn": 0, "rate": 0})
        self.assertFalse(parsed["security_headers"]["enabled"])
        self.assertEqual(parsed["security_headers"]["x_frame_options"], "")

    def test_limit_conn_perip(self):
        text = """
        limit_req_zone $binary_remote_addr zone=ex_req:1m rate=8r/s;
        server {
            listen 80;
            server_name example.com;
            root "/var/www/panel/example.com";
            limit_conn perip 10;
        }
        """
        parsed = parse_site_files(text, primary="example.com", primary_root="/var/www/panel/example.com")
        self.assertEqual(parsed["limit"], {"conn": 10, "rate": 8})
        self.assertEqual(parsed["domains"], [{"domain": "example.com", "port": 80}])

    def test_rewrite_template_comes_from_file(self):
        parsed = parse_site_files(
            "server {\n listen 80;\n server_name example.com;\n root \"/var/www/panel/example.com\";\n}\n",
            primary="example.com",
            primary_root="/var/www/panel/example.com",
            rewrite_text="location / {\n        try_files $uri $uri/ /index.php?$args;\n    }\n",
        )
        self.assertEqual(parsed["preset"], "wordpress")
        self.assertEqual(parsed["rewrite_body"], "try_files $uri $uri/ /index.php?$args;")

    def test_binding_domain_and_redirect(self):
        text = """
        server {
            listen 80;
            server_name example.com extra.example.com;
            root "/var/www/panel/example.com";
            rewrite ^/docs/?(.*)$ https://example.com/docs/$1 permanent;
        }
        server {
            listen 80;
            server_name bind.example.com;
            root "/var/www/panel/example.com/api";
        }
        """
        parsed = parse_site_files(text, primary="example.com", primary_root="/var/www/panel/example.com")
        self.assertEqual(
            parsed["domains"],
            [{"domain": "example.com", "port": 80}, {"domain": "extra.example.com", "port": 80}],
        )
        self.assertEqual(parsed["bindings"], [{"domain": "bind.example.com", "subdir": "api"}])
        self.assertEqual(parsed["redirects"], [{"path": "/docs", "target": "https://example.com/docs", "code": 301}])


if __name__ == "__main__":
    unittest.main()
