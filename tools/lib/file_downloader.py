#!/usr/bin/env python3
"""
CLI tool for downloading files and querying the comma API.
Called by C++ replay/cabana via subprocess.

Subcommands:
  route-files <route>    - Get route file URLs as JSON
  download <url>         - Download URL to local cache, print local path
  devices                - List user's devices as JSON
  device-routes <did>    - List routes for a device as JSON
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import shutil

from openpilot.system.hardware.hw import Paths
from openpilot.tools.lib.api import CommaApi, UnauthorizedError, APIError
from openpilot.tools.lib.auth_config import get_token
from openpilot.tools.lib.url_file import URLFile


def api_call(func):
  """Run an API call, outputting JSON result or error to stdout."""
  try:
    result = func(CommaApi(get_token()))
    json.dump(result, sys.stdout)
  except UnauthorizedError:
    json.dump({"error": "unauthorized"}, sys.stdout)
  except APIError as e:
    error = "not_found" if getattr(e, 'status_code', 0) == 404 else str(e)
    json.dump({"error": error}, sys.stdout)
  except Exception as e:
    json.dump({"error": str(e)}, sys.stdout)
  sys.stdout.write("\n")
  sys.stdout.flush()


def cache_file_path(url):
  url_without_query = url.split("?")[0]
  return os.path.join(Paths.download_cache_root(), hashlib.sha256(url_without_query.encode()).hexdigest())


def fetch_route_files(route, api=None):
  api = api or CommaApi(get_token())
  return api.get(f"v1/route/{route}/files")


def iter_route_file_urls(route_files):
  for value in route_files.values():
    if not isinstance(value, list):
      continue
    for url in value:
      if isinstance(url, str):
        yield url


def download_file(url, use_cache=True):
  if use_cache:
    local_path = cache_file_path(url)
    if os.path.exists(local_path):
      return local_path

  # Stream the file in a single HTTP request instead of making
  # a separate Range request per chunk (which was very slow).
  pool = URLFile.pool_manager()
  r = pool.request("GET", url, preload_content=False)
  try:
    if r.status not in (200, 206):
      raise RuntimeError(f"HTTP {r.status}")

    total = int(r.headers.get('content-length', 0))
    if total <= 0:
      raise RuntimeError("File not found or empty")

    os.makedirs(Paths.download_cache_root(), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=Paths.download_cache_root())
    try:
      downloaded = 0
      chunk_size = 1024 * 1024
      with os.fdopen(tmp_fd, 'wb') as f:
        for data in r.stream(chunk_size):
          f.write(data)
          downloaded += len(data)
          sys.stderr.write(f"PROGRESS:{downloaded}:{total}\n")
          sys.stderr.flush()

      if use_cache:
        shutil.move(tmp_path, local_path)
        return local_path
      return tmp_path
    except Exception:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass
      raise
  finally:
    r.release_conn()


def cmd_route_files(args):
  api_call(lambda api: fetch_route_files(args.route, api))


def cmd_download(args):
  try:
    sys.stdout.write(download_file(args.url, use_cache=not args.no_cache) + "\n")
  except Exception as e:
    sys.stderr.write(f"ERROR:{e}\n")
    sys.stderr.flush()
    sys.exit(1)

  sys.stdout.flush()


def cmd_prefetch_route(args):
  try:
    route_files = fetch_route_files(args.route)
    urls = list(dict.fromkeys(iter_route_file_urls(route_files)))
    for url in urls:
      download_file(url, use_cache=not args.no_cache)
    json.dump({"cached": len(urls)}, sys.stdout)
    sys.stdout.write("\n")
  except UnauthorizedError:
    sys.stderr.write("ERROR:unauthorized\n")
    sys.stderr.flush()
    sys.exit(1)
  except APIError as e:
    error = "not_found" if getattr(e, 'status_code', 0) == 404 else str(e)
    sys.stderr.write(f"ERROR:{error}\n")
    sys.stderr.flush()
    sys.exit(1)
  except Exception as e:
    sys.stderr.write(f"ERROR:{e}\n")
    sys.stderr.flush()
    sys.exit(1)

  sys.stdout.flush()


def cmd_devices(args):
  api_call(lambda api: api.get("v1/me/devices/"))


def cmd_device_routes(args):
  def fetch(api):
    if args.preserved:
      return api.get(f"v1/devices/{args.dongle_id}/routes/preserved")
    params = {}
    if args.start is not None:
      params['start'] = args.start
    if args.end is not None:
      params['end'] = args.end
    return api.get(f"v1/devices/{args.dongle_id}/routes_segments", params=params)
  api_call(fetch)


def main():
  parser = argparse.ArgumentParser(description="File downloader CLI for openpilot tools")
  subparsers = parser.add_subparsers(dest="command", required=True)

  p_rf = subparsers.add_parser("route-files")
  p_rf.add_argument("route")
  p_rf.set_defaults(func=cmd_route_files)

  p_pr = subparsers.add_parser("prefetch-route")
  p_pr.add_argument("route")
  p_pr.add_argument("--no-cache", action="store_true")
  p_pr.set_defaults(func=cmd_prefetch_route)

  p_dl = subparsers.add_parser("download")
  p_dl.add_argument("url")
  p_dl.add_argument("--no-cache", action="store_true")
  p_dl.set_defaults(func=cmd_download)

  p_dev = subparsers.add_parser("devices")
  p_dev.set_defaults(func=cmd_devices)

  p_dr = subparsers.add_parser("device-routes")
  p_dr.add_argument("dongle_id")
  p_dr.add_argument("--start", type=int, default=None)
  p_dr.add_argument("--end", type=int, default=None)
  p_dr.add_argument("--preserved", action="store_true")
  p_dr.set_defaults(func=cmd_device_routes)

  args = parser.parse_args()
  args.func(args)


if __name__ == "__main__":
  main()
