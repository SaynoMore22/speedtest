#!/usr/bin/env python3
"""Замер скорости интернета по скачиванию файла.

Делает N последовательных запросов к заданному URL, считает среднее время
запроса, суммарный объём скачанных данных и среднюю скорость в Мбит/с и МБ/с.
Использует только стандартную библиотеку Python.
"""

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://upload.wikimedia.org/wikipedia/commons/3/3f/Fronalpstock_big.jpg"
CHUNK_SIZE = 64 * 1024
USER_AGENT = "speedtest-script/1.0 (+https://github.com/SaynoMore22/speedtest)"


def fetch(url: str, timeout: float) -> tuple[int, float]:
    """Скачивает URL целиком. Возвращает (кол-во байт, время в секундах)."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            # Просим не отдавать закэшированную копию.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    size = 0
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        expected = response.headers.get("Content-Length")
        while chunk := response.read(CHUNK_SIZE):
            size += len(chunk)
    if expected is not None and expected.isdigit() and size != int(expected):
        raise IOError(f"соединение оборвалось: получено {size} из {expected} байт")
    elapsed = time.perf_counter() - start
    return size, elapsed


def format_bytes(size: float) -> str:
    for unit in ("Б", "КБ", "МБ"):
        if size < 1000:
            return f"{size:.2f} {unit}"
        size /= 1000
    return f"{size:.2f} ГБ"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Замер скорости интернета: N последовательных скачиваний файла по URL."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help="адрес тяжёлого файла (по умолчанию — большая картинка с Wikimedia)",
    )
    parser.add_argument(
        "-n", "--requests", type=int, default=10,
        help="количество запросов (по умолчанию 10)",
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=60.0,
        help="таймаут одного запроса в секундах (по умолчанию 60)",
    )
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("количество запросов должно быть >= 1")
    if args.timeout <= 0:
        parser.error("таймаут должен быть > 0")
    if urllib.parse.urlparse(args.url).scheme not in ("http", "https"):
        parser.error("URL должен начинаться с http:// или https://")
    return args


def main() -> int:
    # Чтобы кириллица не ломалась при перенаправлении вывода в файл/пайп на Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args()
    print(f"URL: {args.url}")
    print(f"Запросов: {args.requests}\n")

    total_bytes = 0
    total_time = 0.0
    ok = 0

    for i in range(1, args.requests + 1):
        try:
            size, elapsed = fetch(args.url, args.timeout)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            print(f"[{i:>2}/{args.requests}] ошибка: {exc}")
            continue
        ok += 1
        total_bytes += size
        total_time += elapsed
        speed = size * 8 / elapsed / 1_000_000 if elapsed > 0 else 0.0
        print(
            f"[{i:>2}/{args.requests}] {format_bytes(size):>10}  "
            f"{elapsed:7.3f} с  {speed:8.2f} Мбит/с"
        )

    if ok == 0:
        print("\nНи один запрос не завершился успешно.", file=sys.stderr)
        return 1

    avg_time = total_time / ok
    mbit_s = total_bytes * 8 / total_time / 1_000_000
    mbyte_s = total_bytes / total_time / 1_000_000

    print("\n===== Итог =====")
    print(f"Успешных запросов:     {ok} из {args.requests}")
    print(f"Скачано всего:         {format_bytes(total_bytes)} ({total_bytes} байт)")
    print(f"Общее время:           {total_time:.3f} с")
    print(f"Среднее время запроса: {avg_time:.3f} с")
    print(f"Средняя скорость:      {mbit_s:.2f} Мбит/с ({mbyte_s:.2f} МБ/с)")
    return 0


def run() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(run())
