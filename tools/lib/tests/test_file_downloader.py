import argparse
import json

from openpilot.tools.lib import file_downloader


def test_iter_route_file_urls_filters_non_lists():
  route_files = {
    "logs": ["https://example.com/a", "https://example.com/b"],
    "cameras": ["https://example.com/c", 123],
    "error": "ignored",
  }

  urls = list(file_downloader.iter_route_file_urls(route_files))

  assert urls == [
    "https://example.com/a",
    "https://example.com/b",
    "https://example.com/c",
  ]


def test_cmd_prefetch_route_downloads_unique_urls(monkeypatch, capsys):
  monkeypatch.setattr(file_downloader, "fetch_route_files", lambda route: {
    "logs": ["https://example.com/a", "https://example.com/b"],
    "cameras": ["https://example.com/b", "https://example.com/c"],
  })

  downloaded = []
  monkeypatch.setattr(
    file_downloader,
    "download_file",
    lambda url, use_cache=True: downloaded.append((url, use_cache)) or f"/tmp/{url.rsplit('/', 1)[-1]}",
  )

  file_downloader.cmd_prefetch_route(argparse.Namespace(route="demo", no_cache=False))

  assert downloaded == [
    ("https://example.com/a", True),
    ("https://example.com/b", True),
    ("https://example.com/c", True),
  ]
  assert json.loads(capsys.readouterr().out) == {"cached": 3}
