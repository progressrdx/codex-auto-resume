"""Small authenticated companion UI. No shell commands or App RPC exposed over HTTP."""
import ipaddress
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
import uuid

STATIC = Path(__file__).with_name('static')
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'),
          '/icon.svg': ('icon.svg', 'image/svg+xml'),
          '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json')}


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def thread_id(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except ValueError:
        raise APIError('请选择一个有效的任务 UUID。')


class Backend:
    """Reuse the guarded CLI, never accept arbitrary paths, prompts or commands."""
    def __init__(self, args):
        self.args = args
        self.quota_lock = threading.Lock()
        self.mutation_lock = threading.Lock()
        self.cached_quota = None
        self.cached_at = 0

    def command(self, *parts):
        cmd = [sys.executable, '-m', 'codex_resume', '--home', str(self.args.home),
               '--app', str(self.args.app), '--state-dir', str(self.args.state_dir), *parts]
        try:
            result = subprocess.run(cmd, cwd=Path(__file__).resolve().parent.parent,
                                    capture_output=True, text=True, timeout=65)
        except subprocess.TimeoutExpired:
            # start may already have spawned a watcher. Never retry a mutation here.
            raise APIError('操作超时，结果未确认。请刷新监控记录；不要重复点击开启。', 504)
        if result.returncode:
            message = result.stderr.strip()
            if not message or len(message) > 300 or 'Traceback' in message:
                message = '操作未成功。请检查 App 是否打开、任务是否可读取，以及本机工具日志。'
            raise APIError(message, 409)
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise APIError('本机工具返回了无法识别的结果；请检查监控记录。', 502)

    def watches(self):
        return self.command('status')

    def threads(self):
        rows = self.command('list')
        return [{'id': row['id'], 'title': row.get('name') or (row.get('preview') or '未命名任务')[:120],
                 'cwd': row.get('cwd', ''), 'archived': row.get('archived', False),
                 'updatedAt': row.get('updatedAt')} for row in rows]

    def check(self, value):
        return self.command('check', thread_id(value))

    def quota(self):
        with self.quota_lock:
            if self.cached_quota is None or time.monotonic() - self.cached_at >= 45:
                raw = self.command('doctor')
                limits = raw.get('rateLimits') or {}
                self.cached_quota = {'app': raw['app'], 'ipc': raw['ipc'],
                    'ready': raw['ready'], 'reason': raw['reason'],
                    'primary': limits.get('primary'), 'secondary': limits.get('secondary'),
                    'limitId': limits.get('limitId'), 'checkedAt': time.time()}
                self.cached_at = time.monotonic()
            return self.cached_quota

    def mutate(self, action, data):
        if not isinstance(data, dict):
            raise APIError('请求必须是 JSON 对象。')
        allowed = {'threadId'} if action == 'stop' else {'threadId', 'maxResumes', 'limitId', 'confirmed'}
        if set(data) - allowed:
            raise APIError('请求包含不支持的字段。')
        target = thread_id(data.get('threadId'))
        if action == 'stop':
            # Serialize stop after an in-flight start, so a late arm cannot undo stop.
            # The CLI start has a bounded timeout; do not report cancellation before it finishes.
            with self.mutation_lock:
                return self.command('stop', target)
        if action != 'start':
            raise APIError('不支持的操作。', 404)
        maximum = data.get('maxResumes', 3)
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise APIError('累计续跑上限应为 1 到 100 的整数。')
        if data.get('confirmed') is not True:
            raise APIError('请先确认只监控所选任务，并了解停止操作的边界。')
        limit = data.get('limitId')
        if limit is not None and (not isinstance(limit, str) or not 1 <= len(limit) <= 100
                                  or limit.startswith('-') or not all(c.isalnum() or c in '_-.' for c in limit)):
            raise APIError('额度桶 ID 格式不正确。')
        if not self.mutation_lock.acquire(blocking=False):
            raise APIError('另一个开启请求仍在处理，请查看监控记录，不要重复提交。', 409)
        try:
            args = ['start', target, '--max-resumes', str(maximum)]
            if limit:
                args += ['--limit-id', limit]
            return self.command(*args)
        finally:
            self.mutation_lock.release()


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    max_workers = 16
    handshake_timeout = 5

    def __init__(self, address, backend, token=None, secure=False, public_origin=None, mini_app_id=None):
        if mini_app_id is not None and not re.fullmatch(r'wx[0-9a-f]{16}', mini_app_id):
            raise RuntimeError('--mini-app-id 必须是明确的微信小程序 AppID。')
        self.mini_app_id = mini_app_id
        authority = None
        if public_origin is not None:
            # A single explicit HTTPS authority, never wildcard Host acceptance.
            match = re.fullmatch(r'https://([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?', public_origin)
            if (not secure or not match or '..' in match[1]
                    or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
                           for label in match[1].split('.'))
                    or (match[2] and not 1 <= int(match[2]) <= 65535)):
                raise RuntimeError('--public-origin 必须是单一 HTTPS 来源（域名和可选端口），且服务必须启用 TLS。')
            authority = public_origin[len('https://'):]
        self.backend = backend
        self.token = token or secrets.token_urlsafe(32)
        self.secure = secure
        self.tls_context = None
        self.worker_slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(address, Handler)
        host, port = self.server_address[:2]
        self.authority = authority or f'{host}:{port}'
        self.origin = f'{"https" if secure else "http"}://{self.authority}'

    def process_request(self, request, client_address):
        # Never queue an unlimited number of sockets/threads. Handshake and HTTP
        # readers consume the same bounded pool; the accept loop never waits.
        if not self.worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            if self.tls_context:
                request.settimeout(self.handshake_timeout)
                request = self.tls_context.wrap_socket(request, server_side=True)
            super().process_request_thread(request, client_address)
        except (OSError, ssl.SSLError):
            self.shutdown_request(request)
        finally:
            self.worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'CodexRelay'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # No URLs, titles, credentials or request bodies in access logs.

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'self'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def guard(self, api=False):
        # Exact authority blocks DNS rebinding and proxy Host confusion. No CORS.
        if self.headers.get_all('Host') != [self.server.authority]:
            raise APIError('访问地址不匹配，请使用启动时提供的地址。', 403)
        origin = self.headers.get('Origin')
        if origin is not None and origin != self.server.origin:
            raise APIError('拒绝来自其他网站的请求。', 403)
        if api:
            tokens = self.headers.get_all('X-Resume-Token') or []
            if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.server.token):
                raise APIError('连接凭据无效或已过期，请重新连接。', 401)
        if self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            # wx.request in DevTools sends Fetch Metadata unlike the native client.
            # This exception is API-only, token-authenticated, exact-AppID and without
            # Origin. Browser cross-origin fetch still fails Origin/preflight checks.
            referer = self.headers.get_all('Referer') or []
            trusted_mini = (api and origin is None and self.server.mini_app_id
                and self.headers.get_all('Sec-Fetch-Site') in (['same-site'], ['cross-site'])
                and len(referer) == 1 and re.fullmatch(
                    r'https://servicewechat\.com/' + self.server.mini_app_id
                    + r'/(?:devtools|[0-9]+)/page-frame\.html', referer[0]))
            if not trusted_mini:
                raise APIError('请直接打开控制台，或确认已为该小程序配置 --mini-app-id。', 403)

    def do_GET(self):
        try:
            path = urlsplit(self.path)
            self.guard(path.path.startswith('/api/'))
            if path.path in ASSETS and not path.query:
                filename, content_type = ASSETS[path.path]
                self.send(200, (STATIC / filename).read_bytes(), content_type)
                return
            if path.path == '/api/watches':
                value = self.server.backend.watches()
            elif path.path == '/api/threads':
                value = self.server.backend.threads()
            elif path.path == '/api/quota':
                value = self.server.backend.quota()
            elif path.path == '/api/check':
                params = parse_qs(path.query)
                if set(params) != {'threadId'} or len(params['threadId']) != 1:
                    raise APIError('需要一个明确的任务 UUID。')
                value = self.server.backend.check(params['threadId'][0])
            else:
                raise APIError('没有这个页面或接口。', 404)
            self.send(200, value)
        except APIError as exc:
            self.send(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.send(500, {'error': '本机服务暂时不可用，请检查服务终端。'})

    def do_POST(self):
        try:
            self.guard(api=True)
            if self.path not in ('/api/start', '/api/stop'):
                raise APIError('不支持的操作。', 404)
            if self.headers.get_content_type() != 'application/json':
                raise APIError('只接受 JSON 请求。', 415)
            lengths = self.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
                raise APIError('请求长度不正确。')
            length = int(lengths[0])
            if not 0 < length <= 2048:
                raise APIError('请求体过大或为空。', 413)
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise APIError('请求不是有效 JSON。')
            self.send(200, self.server.backend.mutate(self.path.rsplit('/', 1)[1], data))
        except APIError as exc:
            self.send(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.send(500, {'error': '操作结果未确认，请刷新监控记录，不要自动重试。'})


def serve(args):
    try:
        host = ipaddress.ip_address(args.host)
    except ValueError:
        raise RuntimeError('--host 必须是明确的本机 IPv4 地址。')
    if host.version != 4 or host.is_unspecified or host.is_multicast:
        raise RuntimeError('只支持绑定明确的 IPv4 地址，不支持通配地址。')
    if bool(args.certfile) != bool(args.keyfile):
        raise RuntimeError('HTTPS 需要同时提供 --certfile 和 --keyfile。')
    if not host.is_loopback and not args.certfile:
        raise RuntimeError('跨设备访问必须提供 HTTPS 证书和私钥；不会开放明文控制接口。')
    context = None
    if args.certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.certfile, args.keyfile)
    with CompanionServer((str(host), args.port), Backend(args), secure=bool(context),
                         public_origin=getattr(args, 'public_origin', None),
                         mini_app_id=getattr(args, 'mini_app_id', None)) as server:
        if context:
            # A TLS-wrapped listening socket handshakes inside accept(), allowing
            # one idle peer to block every other client before Handler's timeout.
            server.tls_context = context
        print(f'控制台地址：{server.origin}/', flush=True)
        print(f'连接凭据：{server.token}', flush=True)
        print('仅向你自己的设备提供凭据。服务重启后凭据失效；网页关闭不会停止监控。', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
