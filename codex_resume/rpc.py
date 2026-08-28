"""Official read-only app-server queries using existing Codex authentication."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time


class ReadOnlyServer:
    METHODS = {'account/rateLimits/read', 'thread/list', 'thread/read'}

    def __init__(self, binary, home, timeout=25):
        self.binary, self.home, self.timeout = str(binary), str(home), timeout
        self.process = None
        self.selector = None
        self.buffer = b''
        self.counter = 0

    def __enter__(self):
        env = os.environ.copy()
        env['CODEX_HOME'] = self.home
        # No shell, no API key handling, no login mutation and no model runs.
        self.process = subprocess.Popen([self.binary, 'app-server', '--stdio'], env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self._request('initialize', {'clientInfo': {'name': 'codex_auto_resume', 'version': '0.1.0'}})
            self._send({'method': 'initialized'})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.selector:
            self.selector.close()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()

    def _send(self, data):
        self.process.stdin.write(json.dumps(data).encode() + b'\n')
        self.process.stdin.flush()

    def _line(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError('额度/任务查询超时')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('只读查询服务已退出')
            self.buffer += chunk
            if len(self.buffer) > 8 * 1024 * 1024:
                raise RuntimeError('只读查询响应过大')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def _request(self, method, params=None):
        self.counter += 1
        request_id = self.counter
        data = {'method': method, 'id': request_id}
        if params is not None:
            data['params'] = params
        self._send(data)
        deadline = time.monotonic() + self.timeout
        while True:
            reply = self._line(deadline)
            if reply.get('id') != request_id or 'method' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError('官方只读接口查询失败；请检查登录和网络状态')
            return reply['result']

    def query(self, method, params=None):
        if method not in self.METHODS:
            raise ValueError('这个连接仅允许读取额度、对话列表和所选对话历史')
        return self._request(method, params)
