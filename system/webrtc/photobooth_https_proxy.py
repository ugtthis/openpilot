#!/usr/bin/env python3

import argparse
import logging
import os
import ssl
import subprocess
from aiohttp import web, ClientSession, ClientTimeout

WEBRTCD_STREAM_URL = os.getenv("WEBRTCD_STREAM_URL", "http://127.0.0.1:5001/stream")
PHOTOBOOTH_PROXY_HOST = os.getenv("PHOTOBOOTH_PROXY_HOST", "0.0.0.0")
PHOTOBOOTH_PROXY_PORT = int(os.getenv("PHOTOBOOTH_PROXY_PORT", "5000"))
PHOTOBOOTH_PROXY_TLS = (os.getenv("PHOTOBOOTH_PROXY_TLS", "0").strip().lower() in ("1", "true", "yes"))
PHOTOBOOTH_CERT_DIR_ENV = os.getenv("PHOTOBOOTH_CERT_DIR", "").strip()
DEFAULT_CERT_DIR_CANDIDATES = [
  "/data/tmp/photobooth_proxy",
  "/tmp/photobooth_proxy",
]

CORS_ALLOW_ORIGIN = "*"
CORS_ALLOW_METHODS = "POST, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type, Authorization"

logger = logging.getLogger("photobooth_https_proxy")


def _apply_cors_headers(resp: web.StreamResponse, request: web.Request) -> web.StreamResponse:
  resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
  resp.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
  resp.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
  resp.headers["Access-Control-Max-Age"] = "600"
  if request.headers.get("Access-Control-Request-Private-Network") == "true":
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
  return resp


@web.middleware
async def cors_middleware(request: web.Request, handler):
  if request.method == "OPTIONS":
    return _apply_cors_headers(web.Response(status=204), request)

  try:
    resp = await handler(request)
  except web.HTTPException as e:
    _apply_cors_headers(e, request)
    raise

  return _apply_cors_headers(resp, request)


def ensure_ssl_cert(cert_path: str, key_path: str) -> None:
  os.makedirs(os.path.dirname(cert_path), exist_ok=True)
  if os.path.exists(cert_path) and os.path.exists(key_path):
    return

  cmd = (
    f'openssl req -x509 -newkey rsa:2048 -nodes -out "{cert_path}" -keyout "{key_path}" '
    '-days 365 -subj "/C=US/ST=California/O=commaai/OU=photobooth/CN=photobooth.local"'
  )
  proc = subprocess.run(cmd, capture_output=True, shell=True, check=False)
  if proc.returncode != 0:
    raise RuntimeError(
      "Failed to create SSL certificate.\n"
      f"[stdout]\n{proc.stdout.decode(errors='replace')}\n"
      f"[stderr]\n{proc.stderr.decode(errors='replace')}"
    )


def create_ssl_context() -> ssl.SSLContext:
  cert_dir_candidates = []
  if PHOTOBOOTH_CERT_DIR_ENV:
    cert_dir_candidates.append(PHOTOBOOTH_CERT_DIR_ENV)
  cert_dir_candidates.extend(DEFAULT_CERT_DIR_CANDIDATES)

  last_exc: Exception | None = None
  for cert_dir in cert_dir_candidates:
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    try:
      ensure_ssl_cert(cert_file, key_file)
      ctx = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_SERVER)
      ctx.load_cert_chain(cert_file, key_file)
      logger.info("Using photobooth TLS cert dir: %s", cert_dir)
      return ctx
    except Exception as e:
      last_exc = e
      logger.warning("Failed to init TLS cert dir %s: %s", cert_dir, e)

  raise RuntimeError("Failed to initialize any photobooth TLS cert dir") from last_exc


async def ping(_request: web.Request):
  return web.json_response({"ok": True})


async def offer(request: web.Request):
  try:
    payload = await request.json()
  except Exception as e:
    raise web.HTTPBadRequest(text="Invalid JSON body") from e

  sdp = payload.get("sdp")
  if not isinstance(sdp, str) or len(sdp.strip()) == 0:
    raise web.HTTPBadRequest(text="Missing or invalid sdp")

  stream_payload = {
    "sdp": sdp,
    "cameras": ["driver"],
    "bridge_services_in": ["soundRequest"],
    "bridge_services_out": [],
    "purpose": "photobooth",
  }

  timeout = ClientTimeout(total=15)
  async with ClientSession(timeout=timeout) as session:
    async with session.post(WEBRTCD_STREAM_URL, json=stream_payload) as resp:
      body = await resp.text()
      if not resp.ok:
        logger.warning("webrtcd /stream failed: status=%s body=%s", resp.status, body[:300])
        raise web.HTTPBadGateway(text=f"webrtcd_error:{resp.status}")
      return web.Response(status=200, text=body, content_type="application/json")


async def on_startup(_app: web.Application):
  scheme = "https" if PHOTOBOOTH_PROXY_TLS else "http"
  logger.info("Photobooth proxy running on %s://%s:%s", scheme, PHOTOBOOTH_PROXY_HOST, PHOTOBOOTH_PROXY_PORT)
  logger.info("Proxying offers to %s", WEBRTCD_STREAM_URL)


def main():
  logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
  parser = argparse.ArgumentParser(description="Photobooth offer proxy")
  parser.add_argument("--host", default=PHOTOBOOTH_PROXY_HOST)
  parser.add_argument("--port", type=int, default=PHOTOBOOTH_PROXY_PORT)
  args = parser.parse_args()

  app = web.Application(middlewares=[cors_middleware])
  app.on_startup.append(on_startup)
  app.router.add_get("/ping", ping)
  app.router.add_post("/offer", offer)
  app.router.add_options("/offer", lambda _req: web.Response(status=204))

  ssl_context = create_ssl_context() if PHOTOBOOTH_PROXY_TLS else None
  web.run_app(app, host=args.host, port=args.port, access_log=None, ssl_context=ssl_context)


if __name__ == "__main__":
  main()
