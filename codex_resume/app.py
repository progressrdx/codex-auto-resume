"""Version-pinned Desktop IPC. Never owns threads or responds to approvals."""
import json
import os
from pathlib import Path
import plistlib
import socket
import stat
import struct
import subprocess
import time
import uuid

from .policy import decide, fingerprint

SUPPORTED_APP = ('26.820.60940', '7119')
MAX_FRAME = 32 * 1024 * 1024
CONTINUATION = ('额度恢复后，请继续本会话原有的未完成工作。先核对当前进度与工作区，'
                '不要重复已完成步骤，不扩大范围，不绕过审批。若工作已经完成或需要用户决定，请明确说明并停止。')


class AppError(RuntimeError):
    pass


class ConnectionUnavailable(AppError):
    """Transient loss of transport; never evidence authorizing a send."""


class ThreadUnavailable(ConnectionUnavailable):
    """App is connected, but the selected conversation is not loaded."""


def check_version(app_path):
    path = Path(app_path)
    try:
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError) as exc:
        raise AppError('找不到可识别的 Codex App') from exc
    version = (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion'))
    if info.get('CFBundleIdentifier') != 'com.openai.codex' or version != SUPPORTED_APP:
        raise AppError('App 版本不在已验证范围内；停止操作，需先适配新版本')
    return {'version': version[0], 'build': version[1]}


def open_selected_thread(app_path, thread_id):
    """Navigate to an existing selected conversation. Never prefill/send a prompt.

    The pinned App's localConversation deep-link handler checks thread existence
    then navigates to its original route. This does not make this client an owner.
    Success here only means navigation was requested; a fresh snapshot is required.
    """
    check_version(app_path)
    target = str(uuid.UUID(thread_id))
    subprocess.run(['/usr/bin/open', '-g', '-a', str(app_path), 'codex://threads/' + target],
                   check=True, timeout=5, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Desktop:
    def __init__(self, home, app_path='/Applications/ChatGPT.app', timeout=12):
        self.home, self.app_path, self.timeout = Path(home), Path(app_path), timeout
        self.sock = None
        self.client_id = 'initializing-client'
        self.owner = None
        self.thread_id = None
        self.snapshot_value = None
        self.revision = None

    def __enter__(self):
        check_version(self.app_path)
        path = self.home / 'ipc/ipc.sock'
        try:
            directory, endpoint = path.parent.lstat(), path.lstat()
            if not stat.S_ISDIR(directory.st_mode) or not stat.S_ISSOCK(endpoint.st_mode):
                raise AppError('IPC 路径不是普通目录中的 socket')
            if any(s.st_uid != os.getuid() or s.st_mode & 0o077 for s in (directory, endpoint)):
                raise AppError('IPC 权限或所有者不安全；不会自动修改权限')
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(str(path))
            result = self.request('initialize', {'clientType': 'codex-auto-resume'}, 0)
            self.client_id = result['result']['clientId']
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __exit__(self, *args):
        self.close()

    def _send(self, msg):
        data = json.dumps(msg, ensure_ascii=False).encode()
        if not 0 < len(data) <= MAX_FRAME:
            raise AppError('IPC 消息过大')
        self.sock.sendall(struct.pack('<I', len(data)) + data)

    def _exact(self, n, deadline):
        chunks, remaining = [], n
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError('App 响应超时')
            self.sock.settimeout(timeout)
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ConnectionUnavailable('App 连接已断开')
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def _read(self, deadline):
        size = struct.unpack('<I', self._exact(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            raise AppError('IPC 消息长度不受支持')
        msg = json.loads(self._exact(size, deadline))
        if not isinstance(msg, dict):
            raise AppError('IPC 消息不是对象')
        if msg.get('type') == 'client-discovery-request':
            self._send({'type': 'client-discovery-response', 'requestId': msg['requestId'],
                        'response': {'canHandle': False}})
        if msg.get('type') == 'request':
            # In particular: never become an approval handler or thread owner.
            self._send({'type': 'response', 'requestId': msg['requestId'],
                        'resultType': 'error', 'error': 'unsupported-by-auto-resume'})
        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-stream-state-changed':
            params = msg.get('params', {})
            if params.get('conversationId') == self.thread_id and params.get('hostId') == 'local' and msg.get('sourceClientId') == self.owner:
                if msg.get('version') != 11:
                    raise AppError('App 状态协议版本已变化')
                change = params.get('change', {})
                if change.get('type') == 'snapshot':
                    self.snapshot_value = change.get('conversationState')
                    self.revision = change.get('revision')
                elif change.get('type') == 'patches':
                    # Do not infer current state from a partial/missed patch stream.
                    self.snapshot_value = None
        return msg

    def request(self, method, params, version, target=None):
        request_id = str(uuid.uuid4())
        msg = {'type': 'request', 'requestId': request_id, 'sourceClientId': self.client_id,
               'method': method, 'params': params, 'version': version,
               'timeoutMs': int(self.timeout * 1000)}
        if target:
            msg['targetClientId'] = target
        self._send(msg)
        deadline = time.monotonic() + self.timeout
        while True:
            reply = self._read(deadline)
            if reply.get('type') != 'response' or reply.get('requestId') != request_id:
                continue
            if reply.get('resultType') != 'success':
                # Only emit safe error codes, never raw remote error strings.
                code = reply.get('error')
                if code == 'no-client-found':
                    raise ThreadUnavailable('所选对话暂未连接到 App')
                raise AppError('App 拒绝请求或接口不兼容；未自动重试')
            if reply.get('method') != method:
                raise AppError('App 响应方法不匹配')
            return reply

    def follow(self, enabled):
        self._send({'type': 'broadcast', 'method': 'thread-stream-following-changed',
                    'sourceClientId': self.client_id, 'targetClientIds': [self.owner], 'version': 1,
                    'params': {'hostId': 'local', 'conversationId': self.thread_id, 'following': enabled}})

    def snapshot(self, thread_id):
        self.thread_id = str(uuid.UUID(thread_id))
        reply = self.request('thread-owner-discovery', {'hostId': 'local', 'conversationId': self.thread_id}, 1)
        self.owner = reply.get('handledByClientId')
        if not isinstance(self.owner, str):
            raise AppError('缺少 App 任务所有者')
        self.follow(False)
        self.snapshot_value = None
        self.follow(True)
        deadline = time.monotonic() + self.timeout
        while self.snapshot_value is None:
            self._read(deadline)
        state = self.snapshot_value
        if not isinstance(state, dict) or state.get('id') != self.thread_id:
            raise AppError('App 返回了不匹配的任务状态')
        return state

    def resume(self, thread_id, expected_fingerprint, message_id):
        check_version(self.app_path)  # Stop if the app updated while we were waiting.
        state = self.snapshot(thread_id)
        if decide(state).action != 'resume' or fingerprint(state) != expected_fingerprint:
            raise AppError('发送前任务状态已改变；取消本次续跑')
        request = {'threadId': thread_id, 'clientUserMessageId': message_id,
                   'input': [{'type': 'text', 'text': CONTINUATION, 'text_elements': []}]}
        reply = self.request('thread-follower-start-turn',
                             {'conversationId': thread_id, 'turnStart': {'request': request}},
                             2, self.owner)
        try:
            turn_id = reply['result']['result']['turn']['id']
            if not isinstance(turn_id, str) or not turn_id:
                raise KeyError('id')
        except (KeyError, TypeError) as exc:
            raise AppError('续跑结果不确定；请在 App 检查，不会自动重发') from exc
        return turn_id
