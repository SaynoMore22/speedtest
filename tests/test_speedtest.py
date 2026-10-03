import io
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import speedtest  # noqa: E402

BIG_BODY = b"x" * (speedtest.CHUNK_SIZE * 3 + 123)


class Handler(BaseHTTPRequestHandler):
    last_headers = None

    def do_GET(self):
        Handler.last_headers = dict(self.headers)
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Length", str(len(BIG_BODY)))
            self.end_headers()
            self.wfile.write(BIG_BODY)
        elif self.path == "/no-length":
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"abc")
            self.close_connection = True
        elif self.path == "/truncated":
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"x" * 1000)
            self.close_connection = True
        elif self.path == "/slow":
            time.sleep(1)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_downloads_whole_body(self):
        with mock.patch.object(speedtest.time, "perf_counter", side_effect=[10.0, 12.5]):
            size, elapsed = speedtest.fetch(self.base + "/big", 5)
        self.assertEqual(size, len(BIG_BODY))
        self.assertEqual(elapsed, 2.5)

    def test_sends_headers(self):
        speedtest.fetch(self.base + "/big", 5)
        headers = Handler.last_headers
        self.assertEqual(
            headers["User-Agent"],
            "speedtest-script/1.0 (+https://github.com/SaynoMore22/speedtest)",
        )
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["Pragma"], "no-cache")

    def test_body_without_content_length(self):
        size, _ = speedtest.fetch(self.base + "/no-length", 5)
        self.assertEqual(size, 3)

    def test_truncated_body_raises(self):
        with self.assertRaises(OSError) as cm:
            speedtest.fetch(self.base + "/truncated", 5)
        self.assertEqual(
            str(cm.exception), "соединение оборвалось: получено 1000 из 100000 байт"
        )

    def test_http_error_raises(self):
        with self.assertRaises(urllib.error.HTTPError):
            speedtest.fetch(self.base + "/missing", 5)

    def test_timeout_is_applied(self):
        with self.assertRaises(OSError):
            speedtest.fetch(self.base + "/slow", 0.2)


class FormatBytesTest(unittest.TestCase):
    def test_units(self):
        cases = {
            0: "0.00 Б",
            999: "999.00 Б",
            1000: "1.00 КБ",
            1_500_000: "1.50 МБ",
            999_999_999: "1000.00 МБ",
            2_000_000_000: "2.00 ГБ",
            5_000_000_000_000: "5000.00 ГБ",
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(speedtest.format_bytes(size), expected)


class ParseArgsTest(unittest.TestCase):
    def parse(self, *argv):
        with mock.patch.object(sys, "argv", ["speedtest.py", *argv]):
            return speedtest.parse_args()

    def assert_error(self, argv, message):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as cm:
            self.parse(*argv)
        self.assertEqual(cm.exception.code, 2)
        last_line = stderr.getvalue().splitlines()[-1]
        self.assertEqual(last_line, f"speedtest.py: error: {message}")

    def test_defaults(self):
        args = self.parse()
        self.assertEqual(args.url, speedtest.DEFAULT_URL)
        self.assertEqual(args.requests, 10)
        self.assertEqual(args.timeout, 60.0)

    def test_custom_values(self):
        args = self.parse("http://a/b.jpg", "-n", "1", "--timeout", "0.5")
        self.assertEqual(args.url, "http://a/b.jpg")
        self.assertEqual(args.requests, 1)
        self.assertEqual(args.timeout, 0.5)
        args = self.parse("https://a/b.jpg", "--requests", "3", "-t", "2")
        self.assertEqual(args.requests, 3)
        self.assertEqual(args.timeout, 2.0)

    def test_help(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit):
            self.parse("--help")
        text = " " + " ".join(stdout.getvalue().split()) + " "
        for phrase in (
            "Замер скорости интернета: N последовательных скачиваний файла по URL.",
            "адрес тяжёлого файла (по умолчанию — большая картинка с Wikimedia)",
            "количество запросов (по умолчанию 10)",
            "таймаут одного запроса в секундах (по умолчанию 60)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(f" {phrase} ", text)

    def test_rejects_bad_requests(self):
        for n in ("0", "-5"):
            with self.subTest(n=n):
                self.assert_error(["-n", n], "количество запросов должно быть >= 1")

    def test_rejects_bad_timeout(self):
        for t in ("0", "-1"):
            with self.subTest(t=t):
                self.assert_error(["-t", t], "таймаут должен быть > 0")

    def test_rejects_bad_url(self):
        for url in ("example.com", "ftp://example.com/f", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assert_error([url], "URL должен начинаться с http:// или https://")


class MainTest(unittest.TestCase):
    def run_main(self, argv, fetch_results):
        fetch = mock.Mock(side_effect=fetch_results)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["speedtest.py", *argv]), \
                mock.patch.object(speedtest, "fetch", fetch), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = speedtest.main()
        return code, stdout.getvalue(), stderr.getvalue(), fetch

    def test_summary(self):
        code, out, err, fetch = self.run_main(
            ["http://h/f", "-n", "2", "-t", "7"],
            [(10_000_000_000, 1.0), (30_000_000_000, 3.0)],
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(fetch.call_args_list, [mock.call("http://h/f", 7.0)] * 2)
        self.assertEqual(
            out,
            "URL: http://h/f\n"
            "Запросов: 2\n"
            "\n"
            "[ 1/2]   10.00 ГБ    1.000 с  80000.00 Мбит/с\n"
            "[ 2/2]   30.00 ГБ    3.000 с  80000.00 Мбит/с\n"
            "\n"
            "===== Итог =====\n"
            "Успешных запросов:     2 из 2\n"
            "Скачано всего:         40.00 ГБ (40000000000 байт)\n"
            "Общее время:           4.000 с\n"
            "Среднее время запроса: 2.000 с\n"
            "Средняя скорость:      80000.00 Мбит/с (10000.00 МБ/с)\n",
        )

    def test_failed_requests_are_skipped(self):
        code, out, _, fetch = self.run_main(
            ["http://h/f", "-n", "3"],
            [
                urllib.error.URLError("dns"),
                (2_000_000, 1.0),
                OSError("обрыв"),
            ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("\n[ 1/3] ошибка: <urlopen error dns>\n", out)
        self.assertIn("\n[ 3/3] ошибка: обрыв\n", out)
        self.assertIn("Успешных запросов:     1 из 3\n", out)
        self.assertIn("Среднее время запроса: 1.000 с\n", out)
        self.assertIn("Средняя скорость:      16.00 Мбит/с (2.00 МБ/с)\n", out)

    def test_zero_elapsed_does_not_crash(self):
        code, out, _, _ = self.run_main(
            ["http://h/f", "-n", "2"], [(1000, 0.0), (1_000_000, 1.0)]
        )
        self.assertEqual(code, 0)
        self.assertIn("[ 1/2]    1.00 КБ    0.000 с      0.00 Мбит/с", out)

    def test_all_failed(self):
        code, out, err, _ = self.run_main(
            ["http://h/f", "-n", "2"], [OSError("a"), OSError("b")]
        )
        self.assertEqual(code, 1)
        self.assertEqual(err, "\nНи один запрос не завершился успешно.\n")
        self.assertNotIn("Итог", out)

    def test_reconfigures_streams_to_utf8(self):
        streams = [mock.Mock(spec=["reconfigure"]) for _ in range(2)]
        with mock.patch.object(sys, "stdout", streams[0]), \
                mock.patch.object(sys, "stderr", streams[1]), \
                mock.patch.object(speedtest, "parse_args", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                speedtest.main()
        for stream in streams:
            stream.reconfigure.assert_called_once_with(encoding="utf-8")


class RunTest(unittest.TestCase):
    def test_returns_main_code(self):
        with mock.patch.object(speedtest, "main", return_value=7):
            self.assertEqual(speedtest.run(), 7)

    def test_keyboard_interrupt(self):
        stderr = io.StringIO()
        with mock.patch.object(speedtest, "main", side_effect=KeyboardInterrupt), \
                redirect_stderr(stderr):
            self.assertEqual(speedtest.run(), 130)
        self.assertEqual(stderr.getvalue(), "\nПрервано пользователем.\n")

    def test_script_entry_point(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "speedtest.py"), "-n", "0"],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
