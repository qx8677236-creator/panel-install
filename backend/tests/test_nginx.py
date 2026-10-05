"""站点配置必须拦住注入，并且在 nginx -t 失败时回到原来的文件。"""

import os
import tempfile
import unittest
from pathlib import Path

from app.files.security import PathJailError
from app.nginx.layout import NginxLayout
from app.nginx.runner import (
    build_certbot_argv,
    nginx_reload_argv,
    nginx_test,
    set_command_runner,
)
from app.nginx.security import SiteError, validate_rewrite
from app.nginx.service import (
    assert_deletable_site_root,
    create_site,
    delete_site,
    issue_certificate,
    list_sites,
    set_enabled,
    set_ssl,
    site_detail,
    update_proxies,
    update_rewrite,
)

TEST_WRAPPER = "/usr/local/sbin/panel-nginx-test"
APPLY_WRAPPER = "/usr/local/sbin/panel-nginx-apply"


def is_check(argv) -> bool:
    return "-t" in argv or TEST_WRAPPER in argv


class NginxSiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "www"
        self.root.mkdir()
        prefix = base / "nginx"
        self.layout = NginxLayout(
            prefix=prefix,
            sites_available=prefix / "sites-available",
            sites_enabled=prefix / "sites-enabled",
            logs=prefix / "logs",
            certs=prefix / "certs",
            file_root=self.root,
            system_mode=False,
        )
        self.calls = []

        def runner(argv):
            self.calls.append(list(argv))
            if is_check(argv):
                chunks = []
                for path in prefix.rglob("*"):
                    if path.is_file() and "conf" in path.name:
                        chunks.append(path.read_text(encoding="utf-8"))
                blob = "\n".join(chunks)
                if "return 410;" in blob:
                    return 1, "nginx: [emerg] forced failure"
                return 0, "nginx: the configuration file syntax is ok"
            return 0, "reload ok"

        self.runner = runner
        set_command_runner(runner)

    def tearDown(self):
        set_command_runner(None)
        self.tmp.cleanup()

    def test_create_writes_site_inside_jail_and_reloads_private_config(self):
        created = create_site("demo.test", "", layout=self.layout)
        self.assertEqual(created["status"], "运行中")
        self.assertEqual(created["root"], "demo.test")
        self.assertTrue((self.root / "demo.test" / "index.html").is_file())
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertIn("server_name demo.test;", conf)
        self.assertIn(str(self.root / "demo.test"), conf)
        self.assertNotIn("/etc/nginx", conf)
        link = self.layout.sites_enabled / "demo.test.conf"
        self.assertTrue(link.is_symlink())
        if os.path.isfile(APPLY_WRAPPER):
            install = [call for call in self.calls if APPLY_WRAPPER in call][-1]
            self.assertEqual(install[-3:-1], ["install", "demo.test"])
            self.assertEqual(install[-1], str(self.layout.sites_available / "demo.test.conf"))
        self.assertNotIn(["/usr/sbin/nginx", "-s", "reload"], self.calls)

    def test_reject_bad_domain_and_system_paths(self):
        with self.assertRaises(SiteError):
            create_site("example.com; }", "demo", layout=self.layout)
        with self.assertRaises(PathJailError):
            create_site("demo.test", "/etc/passwd", layout=self.layout)
        with self.assertRaises(PathJailError):
            create_site("demo.test", "../outside", layout=self.layout)
        self.assertFalse((self.layout.sites_available / "demo.test.conf").exists())

    def test_failed_check_rolls_back_create_and_update(self):
        def fail_all(argv):
            self.calls.append(list(argv))
            if is_check(argv):
                return 1, "nginx: [emerg] unexpected end of file"
            return 0, "ok"

        set_command_runner(fail_all)
        with self.assertRaises(SiteError) as caught:
            create_site("gone.test", "", layout=self.layout)
        self.assertIn("回滚", str(caught.exception))
        self.assertEqual(caught.exception.log, "nginx: [emerg] unexpected end of file")
        self.assertFalse((self.layout.sites_available / "gone.test.conf").exists())
        self.assertEqual(list_sites(layout=self.layout)["sites"], [])

        set_command_runner(self.runner)
        create_site("demo.test", "", layout=self.layout)
        with self.assertRaises(SiteError):
            update_rewrite("demo.test", "custom", "return 410;", layout=self.layout)
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertNotIn("return 410;", conf)
        rewrite = (self.layout.prefix / "rewrite" / "demo.test.conf").read_text(encoding="utf-8")
        self.assertNotIn("return 410;", rewrite)
        self.assertIn("try_files $uri $uri/ /index.html;", rewrite)
        detail = site_detail("demo.test", layout=self.layout)
        self.assertIn("forced failure", detail["last_log"])

    def test_disable_moves_link_and_second_save_keeps_backup(self):
        create_site("demo.test", "", layout=self.layout)
        stopped = set_enabled("demo.test", False, layout=self.layout)
        self.assertEqual(stopped["status"], "已停止")
        self.assertFalse((self.layout.sites_enabled / "demo.test.conf").exists())
        self.assertTrue((self.layout.sites_available / "demo.test.conf").is_file())
        updated = update_rewrite("demo.test", "wordpress", "", layout=self.layout)
        self.assertEqual(updated["backup"], "已备份")
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        rewrite = (self.layout.prefix / "rewrite" / "demo.test.conf").read_text(encoding="utf-8")
        self.assertIn('include "', conf)
        self.assertIn("index.php?$args", rewrite)
        self.assertTrue((self.layout.sites_available / "demo.test.conf.bak").is_file())

    def test_rewrite_and_proxy_reject_injection(self):
        create_site("demo.test", "", layout=self.layout)
        with self.assertRaises(SiteError):
            validate_rewrite("include /etc/nginx/fastcgi.conf;")
        with self.assertRaises(SiteError):
            update_rewrite("demo.test", "custom", "alias /etc/nginx;", layout=self.layout)
        with self.assertRaises(SiteError):
            update_proxies(
                "demo.test",
                [{"path": "/api", "upstream": "http://127.0.0.1:8080; }"}],
                layout=self.layout,
            )
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertNotIn("alias", conf)
        self.assertNotIn("proxy_pass", conf)
        saved = update_proxies(
            "demo.test",
            [{"path": "/api/", "upstream": "http://127.0.0.1:8080"}],
            layout=self.layout,
        )
        self.assertEqual(saved["proxies"][0]["upstream"], "http://127.0.0.1:8080")
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertEqual(conf.count("proxy_pass"), 1)

    def test_ssl_requires_a_real_cert_file(self):
        create_site("demo.test", "", layout=self.layout)
        with self.assertRaises(SiteError):
            set_ssl("demo.test", True, layout=self.layout)
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertNotIn("listen 443", conf)

        cert_dir = self.layout.certs / "demo.test"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("dummy", encoding="utf-8")
        (cert_dir / "privkey.pem").write_text("dummy", encoding="utf-8")
        enabled = set_ssl("demo.test", True, layout=self.layout)
        self.assertTrue(enabled["ssl"])
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertIn("listen 443 ssl;", conf)
        self.assertIn("fullchain.pem", conf)

    def test_certbot_argv_is_fixed_and_issue_does_not_invent_a_cert(self):
        webroot = self.root / "demo.test"
        webroot.mkdir()
        argv = build_certbot_argv("/usr/bin/certbot", "demo.test", webroot, "ops@example.com")
        self.assertEqual(argv[0], "/usr/bin/certbot")
        self.assertIn("certonly", argv)
        self.assertNotIn("--nginx", argv)
        self.assertNotIn(";", " ".join(argv))
        with self.assertRaises(SiteError):
            build_certbot_argv("/usr/bin/certbot", "bad domain", webroot, "ops@example.com")

        create_site("demo.test", "", layout=self.layout)

        def runner(argv):
            self.calls.append(list(argv))
            if argv[0].endswith("certbot"):
                return 0, "simulated certbot"
            return 0, "ok"

        set_command_runner(runner)
        with self.assertRaises(SiteError) as caught:
            issue_certificate("demo.test", "ops@example.com", layout=self.layout)
        self.assertIn("没有在允许的目录", str(caught.exception))
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertNotIn("listen 443", conf)
        self.assertTrue(any(call[0].endswith("certbot") for call in self.calls))

    def test_explicit_binary_is_allowed_outside_default_directories(self):
        script = Path(self.tmp.name) / "nginx"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        from app.nginx.runner import _find

        self.assertEqual(_find("nginx", str(script)), str(script.resolve()))
        self.assertIsNone(_find("rm", str(script)))

    def test_system_reload_argv_is_opt_in(self):
        isolated = nginx_reload_argv(self.layout, "/usr/sbin/nginx")
        self.assertEqual(isolated[1:3], ["-s", "reload"])
        self.assertIn("-c", isolated)
        system = NginxLayout(
            prefix=self.layout.prefix,
            sites_available=self.layout.sites_available,
            sites_enabled=self.layout.sites_enabled,
            logs=self.layout.logs,
            certs=self.layout.certs,
            file_root=self.layout.file_root,
            system_mode=True,
        )
        self.assertEqual(nginx_reload_argv(system, "/usr/sbin/nginx"), ["/usr/sbin/nginx", "-s", "reload"])

    def test_real_nginx_accepts_isolated_config(self):
        import shutil

        if not shutil.which("nginx"):
            self.skipTest("本机没有 nginx")
        if os.geteuid() != 0 and os.path.isfile(TEST_WRAPPER):
            self.skipTest("普通用户不能绑定 80，生产检查走 panel-nginx-test 的独立网络命名空间")
        create_site("demo.test", "", layout=self.layout)
        set_command_runner(None)
        code, output = nginx_test(self.layout)
        self.assertEqual(code, 0, output)

    def test_domains_bindings_and_raw_path_guard(self):
        from app.nginx.service import save_raw_config, update_bindings, update_domains

        create_site("demo.test", "", layout=self.layout)
        saved = update_domains("demo.test", ["demo.test", "www.demo.test"], layout=self.layout)
        self.assertEqual(
            saved["domains"],
            [{"domain": "demo.test", "port": 80}, {"domain": "www.demo.test", "port": 80}],
        )
        bound = update_bindings(
            "demo.test",
            [{"domain": "api.demo.test", "subdir": "api"}],
            layout=self.layout,
        )
        self.assertEqual(bound["bindings"][0]["subdir"], "api")
        self.assertTrue((self.root / "demo.test" / "api").is_dir())
        with self.assertRaises(SiteError):
            update_bindings("demo.test", [{"domain": "bad.demo.test", "subdir": "../../etc"}], layout=self.layout)
        conf = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        with self.assertRaises(SiteError):
            save_raw_config("demo.test", conf.replace(str(self.root), "/etc"), layout=self.layout)
        kept = (self.layout.sites_available / "demo.test.conf").read_text(encoding="utf-8")
        self.assertIn(str(self.root), kept)

    def test_explicit_port_finds_site_stored_without_port(self):
        from app.nginx.service import _resolve

        state = {
            "demo.test": {"domain": "demo.test", "domains": [{"domain": "demo.test", "port": 9999}]},
            "demo.test:1991": {"domain": "demo.test:1991", "domains": [{"domain": "demo.test", "port": 1991}]},
        }
        self.assertEqual(_resolve(state, "demo.test:9999"), "demo.test")
        self.assertEqual(_resolve(state, "demo.test:1991"), "demo.test:1991")
        with self.assertRaises(SiteError):
            _resolve(state, "demo.test:8081")

    def test_access_password_is_not_stored_in_clear_text(self):
        from app.nginx.service import update_access

        create_site("demo.test", "", layout=self.layout)
        saved = update_access(
            "demo.test",
            True,
            "Restricted",
            [{"name": "editor", "password": "secret-pass"}],
            layout=self.layout,
        )
        self.assertEqual(saved["auth"]["users"], ["editor"])
        self.assertNotIn("secret-pass", str(saved))
        stored = (self.layout.prefix / "passwords" / "demo.test.htpasswd").read_text(encoding="utf-8")
        self.assertTrue(stored.startswith("editor:$apr1$"))
        self.assertNotIn("secret-pass", stored)

    def test_delete_refuses_bad_confirm_and_unsafe_roots(self):
        from fastapi import HTTPException

        from app.nginx.router import _call
        from app.nginx.service import _load, _save

        key = "delcheck.local:29173"
        self.layout.prefix.mkdir(parents=True, exist_ok=True)
        safe = self.root / "delcheck.local_29173"
        safe.mkdir()
        _save(self.layout, {key: {"domain": key, "root": "delcheck.local_29173", "enabled": False}})
        for wrong in ("", "wrong", " delcheck.local:29173"):
            with self.assertRaises(SiteError) as caught:
                delete_site(key, True, True, wrong, "admin", "127.0.0.1", layout=self.layout)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(str(caught.exception), "请输入站点域名")
        self.assertIn(key, _load(self.layout))
        self.assertEqual(self.calls, [])
        self.assertTrue(safe.is_dir())

        with self.assertRaises(SiteError) as missing:
            delete_site("missing.local:29175", False, False, "missing.local:29175", "admin", "127.0.0.1", layout=self.layout)
        self.assertEqual(missing.exception.status, 404)

        def boom():
            raise PathJailError("sensitive_path")

        with self.assertRaises(HTTPException) as http_caught:
            _call(boom)
        self.assertEqual(http_caught.exception.status_code, 403)

        unsafe = [
            "/",
            "/etc",
            "/etc/passwd",
            self.root.as_posix() + "/../../etc/passwd",
            str(self.root),
        ]
        for raw in unsafe:
            with self.assertRaises(PathJailError):
                assert_deletable_site_root(raw, self.root)
            _save(self.layout, {key: {"domain": key, "root": raw, "enabled": False}})
            with self.assertRaises(PathJailError):
                delete_site(key, True, False, key, "admin", "127.0.0.1", layout=self.layout)
            self.assertEqual(self.calls, [])
            self.assertIn(key, _load(self.layout))
            self.assertTrue(safe.is_dir())

        outside = Path(self.tmp.name) / "outside-jail"
        outside.mkdir()
        link = self.root / "escape-link"
        link.symlink_to(outside, target_is_directory=True)
        nested = self.root / "nested-site"
        nested.mkdir()
        (nested / "leak").symlink_to(outside, target_is_directory=True)
        for raw in ("escape-link", "nested-site"):
            with self.assertRaises(PathJailError):
                assert_deletable_site_root(raw, self.root)
            _save(self.layout, {key: {"domain": key, "root": raw, "enabled": False}})
            with self.assertRaises(PathJailError):
                delete_site(key, True, False, key, "admin", "127.0.0.1", layout=self.layout)
        self.assertEqual(self.calls, [])
        self.assertTrue(link.is_symlink())
        self.assertTrue(outside.is_dir())
        self.assertTrue((nested / "leak").is_symlink())

    def test_delete_checks_config_before_remove(self):
        if not os.path.isfile(APPLY_WRAPPER):
            self.skipTest("没有 panel-nginx-apply")
        from app.backup import store as backup_store
        from app.logs import store as log_store

        recorded = []
        original = log_store.write_log
        original_forget = backup_store.forget_site_tasks

        def capture(operator, log_type, details, ip):
            recorded.append((operator, log_type, details, ip))

        log_store.write_log = capture
        backup_store.forget_site_tasks = lambda target: 0
        try:
            create_site("keep.local:29174", "", layout=self.layout)
            gone = create_site("delcheck.local:29173", "", layout=self.layout)
            self.assertEqual(gone["domain"], "delcheck.local:29173")
            root = self.root / "delcheck.local_29173"
            self.assertTrue(root.is_dir())
            (self.root / "keep.txt").write_text("stay", encoding="utf-8")
            stem = "delcheck.local.29173"
            access = self.layout.logs / f"{stem}.access.log"
            error = self.layout.logs / f"{stem}.error.log"
            access.write_text("a", encoding="utf-8")
            error.write_text("e", encoding="utf-8")
            decoy = self.layout.logs / "keep.local.29174.access.log"
            decoy.write_text("k", encoding="utf-8")
            cert = self.layout.certs / stem
            cert.mkdir(parents=True)
            (cert / "fullchain.pem").write_text("pem", encoding="utf-8")
            (cert / "privkey.pem").write_text("key", encoding="utf-8")
            before = len(self.calls)
            result = delete_site(
                gone["domain"],
                True,
                True,
                gone["domain"],
                "admin",
                "127.0.0.1",
                layout=self.layout,
            )
            self.assertIn("没有关联数据库", result["message"])
            later = self.calls[before:]
            self.assertTrue(
                any(APPLY_WRAPPER in call and "discard" in call for call in later)
            )
            self.assertTrue((self.layout.prefix / "nginx.conf").is_file())
            self.assertFalse((self.layout.sites_available / f"{stem}.conf").exists())
            self.assertTrue((self.layout.sites_available / "keep.local.29174.conf").is_file())
            self.assertFalse(root.exists())
            self.assertTrue((self.root / "keep.local_29174").is_dir())
            self.assertEqual((self.root / "keep.txt").read_text(encoding="utf-8"), "stay")
            self.assertFalse(access.exists())
            self.assertFalse(error.exists())
            self.assertTrue(decoy.is_file())
            self.assertTrue(self.layout.logs.is_dir())
            self.assertFalse((cert / "fullchain.pem").exists())
            names = [item["domain"] for item in list_sites(layout=self.layout)["sites"]]
            self.assertNotIn(gone["domain"], names)
            self.assertIn("keep.local:29174", names)
            self.assertTrue(recorded)
            self.assertTrue(recorded[-1][2].startswith("管理员彻底删除了站点 [delcheck.local:29173]，并清理了相关残留"))
        finally:
            log_store.write_log = original
            backup_store.forget_site_tasks = original_forget

    def test_failed_reload_still_drops_site_nginx_conf(self):
        create_site("keep.local:29174", "", layout=self.layout)
        create_site("delcheck.local:29173", "", layout=self.layout)

        def fail_checks(argv):
            self.calls.append(list(argv))
            if is_check(argv):
                return 1, "nginx: [emerg] forced failure"
            return 1, "reload failed"

        set_command_runner(fail_checks)
        result = delete_site(
            "delcheck.local:29173",
            False,
            False,
            "delcheck.local:29173",
            "admin",
            "127.0.0.1",
            layout=self.layout,
        )
        if os.path.isfile(APPLY_WRAPPER):
            self.assertIn("没有重载", result["message"])
        self.assertFalse((self.layout.sites_available / "delcheck.local.29173.conf").exists())
        self.assertFalse((self.layout.sites_enabled / "delcheck.local.29173.conf").exists())
        self.assertFalse((self.layout.prefix / "rewrite" / "delcheck.local.29173.conf").exists())
        self.assertTrue((self.layout.sites_available / "keep.local.29174.conf").is_file())
        self.assertTrue((self.layout.prefix / "nginx.conf").is_file())
        self.assertNotIn("delcheck.local:29173", (self.layout.prefix / "nginx.conf").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "delcheck.local_29173").is_dir())
        names = [item["domain"] for item in list_sites(layout=self.layout)["sites"]]
        self.assertNotIn("delcheck.local:29173", names)
        self.assertIn("keep.local:29174", names)

    def test_bound_database_failure_keeps_site_record(self):
        from app.databases import service as database_service
        from app.databases.security import DatabaseError
        from app.nginx.service import _load, _save

        key = "delcheck.local:29173"
        create_site(key, "", layout=self.layout)
        state = _load(self.layout)
        state[key]["database"] = {"engine": "mysql", "name": "not_a_real_db"}
        _save(self.layout, state)
        original = database_service.delete_database

        def unavailable(name):
            raise DatabaseError("当前未安装 Mysql 环境/远程数据库")

        database_service.delete_database = unavailable
        try:
            with self.assertRaises(SiteError) as caught:
                delete_site(key, False, True, key, "admin", "127.0.0.1", layout=self.layout)
            self.assertEqual(str(caught.exception), "nginx 已移除但数据库删除失败，站点记录保留")
            self.assertIn(key, _load(self.layout))
        finally:
            database_service.delete_database = original

