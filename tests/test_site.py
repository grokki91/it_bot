# -*- coding: utf-8 -*-
"""Команда `site`: конфиг обратного прокси — свой домен, 443 и сертификат.

Домена в репозитории нет и быть не должно: его называют в командной строке
или в ND_SITE_DOMAIN, а конфиг с ним остаётся на сервере владельца. Здесь
проверяется только то, что из названного домена собирается рабочий конфиг,
а из неназванного или подложного — ничего.
"""
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import cli  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

DOMAIN = "site.example.org"


class SiteCase(unittest.TestCase):
    def run_cmd(self, argv, code=0):
        args = cli.build_parser().parse_args(argv)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(args.func(args), code)
        return out.getvalue()

    def config(self, argv=()):
        return self.run_cmd(["site", "--domain", DOMAIN] + list(argv))

    def with_cert(self, ready, argv=()):
        """Тот же конфиг, но при известном ответе «сертификат уже есть?»."""
        saved = cli.cert_ready
        cli.cert_ready = lambda domain: ready
        try:
            return self.config(argv)
        finally:
            cli.cert_ready = saved

    # ---------------------------------------------------------------- http2
    # Написание http2 менялось: до nginx 1.25.1 это слово в самой listen,
    # после — отдельная директива. Ошибиться нельзя: nginx не запустится
    # вовсе, а с ним ляжет и всё остальное, что стоит на машине.
    def test_old_nginx_gets_http2_inside_listen(self):
        self.assertEqual(cli.listen_443((1, 18, 0)).count("ssl http2;"), 2)
        self.assertNotIn("http2 on;", cli.listen_443((1, 18, 0)))

    def test_new_nginx_gets_the_directive(self):
        lines = cli.listen_443((1, 27, 0))
        self.assertIn("http2 on;", lines)
        self.assertNotIn("ssl http2;", lines)

    def test_the_version_of_the_change_counts_as_new(self):
        self.assertIn("http2 on;", cli.listen_443((1, 25, 1)))
        self.assertIn("ssl http2;", cli.listen_443((1, 25, 0)))

    def test_without_nginx_the_compatible_form_is_written(self):
        # версии не видно — пишем так, как поймут обе
        self.assertIn("ssl http2;", cli.listen_443(None))

    def test_the_config_follows_the_installed_nginx(self):
        saved = cli.nginx_version
        cli.nginx_version = lambda: (1, 18, 0)
        try:
            self.assertIn("listen 443 ssl http2;", self.config())
        finally:
            cli.nginx_version = saved

    # ------------------------------------------------------------- сам конфиг
    def test_https_port_is_open_and_http_only_redirects(self):
        text = self.config()
        self.assertIn("listen 443 ssl", text)
        self.assertIn("return 301 https://$host$request_uri;", text)

    def test_certificate_is_taken_from_letsencrypt_by_domain(self):
        text = self.config()
        self.assertIn("/etc/letsencrypt/live/%s/fullchain.pem" % DOMAIN, text)
        self.assertIn("/etc/letsencrypt/live/%s/privkey.pem" % DOMAIN, text)

    def test_acme_check_still_goes_by_http(self):
        # без этого места certbot не продлит сертификат: проверка ходит на 80
        self.assertIn("location /.well-known/acme-challenge/", self.config())

    def test_html_is_not_listed_among_gzip_types(self):
        # nginx сжимает text/html всегда, а на повтор ворчит в `nginx -t`
        text = self.config()
        listed = text.split("gzip_types", 1)[1].split(";", 1)[0]
        self.assertIn("application/rss+xml", listed)
        self.assertNotIn("text/html", listed)

    def test_page_is_proxied_to_itself(self):
        text = self.config(["--port", "8123"])
        self.assertIn("proxy_pass http://127.0.0.1:8123;", text)

    def test_proxy_sets_the_headers_the_page_believes(self):
        text = self.config()
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", text)
        self.assertIn("proxy_set_header X-Forwarded-For "
                      "$proxy_add_x_forwarded_for;", text)

    def test_port_defaults_to_the_one_the_page_listens_on(self):
        saved = CFG["web_port"]
        CFG["web_port"] = 9099
        try:
            self.assertIn("proxy_pass http://127.0.0.1:9099;", self.config())
        finally:
            CFG["web_port"] = saved

    def test_file_is_saved_without_the_domain_in_its_name(self):
        self.config()
        path = cli.HOME / "nginx-site.conf"
        self.assertTrue(path.exists())
        self.assertIn(DOMAIN, path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- домен
    def test_domain_comes_from_the_environment_too(self):
        os.environ["ND_SITE_DOMAIN"] = DOMAIN
        try:
            self.assertIn("server_name %s;" % DOMAIN, self.run_cmd(["site"]))
        finally:
            del os.environ["ND_SITE_DOMAIN"]

    def test_domain_is_tidied_up(self):
        text = self.run_cmd(["site", "--domain", "HTTPS://Site.Example.ORG./"])
        self.assertIn("server_name %s;" % DOMAIN, text)

    def test_path_from_the_address_bar_is_dropped(self):
        text = self.run_cmd(["site", "--domain", "site.example.org/news?x=1"])
        self.assertIn("server_name %s;" % DOMAIN, text)

    def test_nameless_run_explains_instead_of_guessing(self):
        os.environ.pop("ND_SITE_DOMAIN", None)
        self.assertIn("--domain", self.run_cmd(["site"], code=2))

    def test_anything_but_a_domain_is_refused(self):
        # строка попадает в директиву nginx как есть — чужому в ней не место
        for bad in ("не домен", "example.com;\nserver_name evil.tld",
                    "example.com ssl", "localhost", "-x.com",
                    "a" * 300 + ".com"):
            # через «=», иначе argparse примет «-x.com» за свой ключ
            text = self.run_cmd(["site", "--domain=" + bad], code=2)
            self.assertIn("не похоже на домен", text)

    # ------------------------------------------------------------------ env
    def applied(self, argv, ready=True):
        """Что команда записала в env (перехватываем, файла не трогаем)."""
        written = {}
        saved_write, saved_cert = cli.write_env, cli.cert_ready
        cli.write_env = lambda values: written.update(values)
        cli.cert_ready = lambda domain: ready
        try:
            text = self.config(argv)
        finally:
            cli.write_env, cli.cert_ready = saved_write, saved_cert
        return written, text

    def test_env_is_left_alone_unless_asked(self):
        text = self.config()
        self.assertIn("ND_WEB_PROXY=1", text)      # сказано, но не сделано
        self.assertNotIn("записано", text)

    def test_apply_env_puts_the_page_behind_the_proxy(self):
        written, _text = self.applied(["--apply-env"])
        self.assertEqual(written.get("ND_WEB_HOST"), "127.0.0.1")
        self.assertEqual(written.get("ND_WEB_PROXY"), "1")

    def test_without_a_certificate_env_is_not_touched(self):
        # иначе страница уйдёт на localhost, а отдавать её наружу будет некому:
        # прокси без сертификата не запустится — и она пропадёт отовсюду
        written, text = self.applied(["--apply-env"], ready=False)
        self.assertEqual(written, {})
        self.assertIn("ENV НЕ ТРОНУТ", text)

    def test_an_unreadable_letsencrypt_does_not_block(self):
        # каталог закрыт от всех, кроме root: «не вижу» — это не «нет»,
        # и запрещать по нему нельзя
        written, text = self.applied(["--apply-env"], ready=None)
        self.assertEqual(written.get("ND_WEB_PROXY"), "1")
        self.assertIn("не видно", text)

    def test_force_applies_env_anyway(self):
        written, _text = self.applied(["--apply-env", "--force"], ready=False)
        self.assertEqual(written.get("ND_WEB_PROXY"), "1")

    # --------------------------------------------------------- порядок шагов
    def test_a_closed_letsencrypt_is_not_an_error(self):
        # Path.exists() на закрытом каталоге кидает PermissionError — команда
        # падала с traceback ровно там, где сертификат уже был получен
        import pathlib
        saved = pathlib.Path.exists

        def deny(self):
            raise PermissionError(13, "Permission denied")

        pathlib.Path.exists = deny
        try:
            self.assertIsNone(cli.cert_ready(DOMAIN))
        finally:
            pathlib.Path.exists = saved

    def test_a_missing_certificate_is_said_out_loud(self):
        text = self.with_cert(False)
        self.assertIn("ставить рано", text)

    def test_with_a_certificate_nothing_is_nagged(self):
        self.assertNotIn("ставить рано", self.with_cert(True))


if __name__ == "__main__":
    unittest.main()
