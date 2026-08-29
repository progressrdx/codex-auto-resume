import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .app import Desktop, check_version, open_selected_thread
from .controller import Controller
from .policy import quota_status
from .rpc import ReadOnlyServer
from .store import Store
from .tasks import inspect_task, list_conversations, stored_assessment


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('需要准确的任务 UUID') from exc


def positive(value):
    n = int(value)
    if not 1 <= n <= 100:
        raise argparse.ArgumentTypeError('次数应在 1 到 100 之间')
    return n


def parser():
    p = argparse.ArgumentParser(description='Codex App 额度恢复续跑（macOS 实验版）')
    p.add_argument('--home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    p.add_argument('--app', type=Path, default=Path('/Applications/ChatGPT.app'))
    p.add_argument('--state-dir', type=Path, default=Path.home() / '.codex-auto-resume')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='只读检查 App 版本、连接和真实额度')
    sub.add_parser('list', help='分页读取所有可用本地对话，包含归档；不会自动托管')
    sub.add_parser('status', help='查看监控记录')
    web = sub.add_parser('web', help='启动桌面/手机浏览器控制台（默认仅本机）')
    web.add_argument('--host', default='127.0.0.1', help='明确的本机 IPv4 地址；非回环地址必须配置 HTTPS')
    web.add_argument('--port', type=int, default=8765)
    web.add_argument('--certfile', type=Path)
    web.add_argument('--keyfile', type=Path)
    web.add_argument('--public-origin', help='启用 TLS 时指定唯一 HTTPS 访问来源，例如 https://relay.example.com:8765')
    web.add_argument('--mini-app-id', help='仅允许这个微信 AppID 的带凭据请求通过模拟器 Fetch Metadata 检查')
    check = sub.add_parser('check', help='只读识别所选对话的任务状态与托管资格')
    check.add_argument('thread', type=identifier)
    stop = sub.add_parser('stop', help='停止后续自动续跑，不打断正在执行的 App 任务')
    stop.add_argument('thread', type=identifier)
    for name in ('start', '_watch'):
        item = sub.add_parser(name, help='后台监控一个选定任务' if name == 'start' else '内部后台命令，请使用 start 启动')
        item.add_argument('thread', type=identifier)
        item.add_argument('--max-resumes', type=positive, default=3, help='该任务累计尝试次数上限（默认 3）')
        item.add_argument('--limit-id', default=None, help='多个额度桶时显式选择；单 codex 桶自动识别')
        if name == '_watch':
            item.add_argument('--lock-fd', type=int, default=None, help=argparse.SUPPRESS)
    return p


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def watch_process_spec(args, lock_fd, frozen=None):
    """Build an independent watcher command for source and packaged launches."""
    if frozen is None:
        frozen = bool(getattr(sys, 'frozen', False))
    prefix = [sys.executable] if frozen else [sys.executable, '-m', 'codex_resume']
    cmd = prefix + ['--home', str(args.home), '--app', str(args.app),
                    '--state-dir', str(args.state_dir), '_watch', args.thread,
                    '--max-resumes', str(args.max_resumes), '--lock-fd', str(lock_fd)]
    if args.limit_id:
        cmd += ['--limit-id', args.limit_id]
    if frozen:
        env = os.environ.copy()
        # A PyInstaller child must unpack/start as a new instance, not inherit
        # the parent's temporary runtime environment.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        cwd = Path(sys.executable).resolve().parent
    else:
        env = None
        cwd = Path(__file__).resolve().parent.parent
    return cmd, cwd, env


def serve_watch(args, store):
    store.lock(args.thread, inherited_fd=args.lock_fd)
    quota = None
    def read_quota():
        nonlocal quota
        if quota is None:
            reader = ReadOnlyServer(args.app / 'Contents/Resources/codex', args.home)
            reader.__enter__()
            quota = reader
        try:
            return quota.query('account/rateLimits/read')
        except Exception:
            quota.__exit__(None, None, None)
            quota = None
            raise
    def load_original(tid):
        # Revalidate eligibility before navigation as well as before dispatch.
        with ReadOnlyServer(args.app / 'Contents/Resources/codex', args.home) as reader:
            if any(r['id'] == tid for r in list_conversations(reader, (True,))):
                return False
            saved = reader.query('thread/read', {'threadId': tid, 'includeTurns': True})
        if not stored_assessment(saved.get('thread'), tid)['canMonitor']:
            return False
        if not store.get(tid)['enabled']:
            return False
        open_selected_thread(args.app, tid)
    controller = Controller(args.thread, store, lambda: Desktop(args.home, args.app), read_quota, args.limit_id,
                            open_thread=load_original)
    previous = None
    try:
        while controller.step():
            state = store.get(args.thread)
            status = (state['status'], state['reason'])
            if status != previous:
                output({'time': time.time(), 'status': status[0], 'reason': status[1]})
                previous = status
            # Small cancellable sleeps; after wake from system sleep step() rechecks everything.
            for _ in range(30):
                if not store.get(args.thread)['enabled']:
                    return
                time.sleep(1)
        state = store.get(args.thread)
        output({'status': state['status'], 'reason': state['reason']})
    finally:
        if quota:
            quota.__exit__(None, None, None)


def main(argv=None):
    args = parser().parse_args(argv)
    args.home, args.app, args.state_dir = [p.expanduser().absolute() for p in (args.home, args.app, args.state_dir)]
    if args.command == 'web':
        from .web import serve
        serve(args)
        return
    if args.command in ('doctor', 'check', 'list', 'start', '_watch'):
        version = check_version(args.app)
    if args.command == 'check':
        result = inspect_task(args.home, args.app, args.thread)
        output(dict(result, note='只读检查；没有打开任务、开启托管或发送消息'))
        return
    if args.command in ('doctor', 'list'):
        with ReadOnlyServer(args.app / 'Contents/Resources/codex', args.home) as server:
            if args.command == 'list':
                output(list_conversations(server))
                return
            with Desktop(args.home, args.app):
                pass
            quota = server.query('account/rateLimits/read')
            ready, next_check, reason = quota_status(quota, time.time())
            output({'app': version, 'ipc': 'connected', 'quotaRead': 'ok',
                    'ready': ready, 'nextCheck': next_check, 'reason': reason,
                    'rateLimits': quota.get('rateLimits'),
                    'note': '只读探测成功不代表某个具体任务已可续跑'})
        return
    store = Store(args.state_dir)
    try:
        if args.command == 'status':
            rows = store.all()
            for row in rows:
                row['attempts'] = store.count(row['thread_id'])
                if row.get('pid') and row['enabled']:
                    try:
                        os.kill(row['pid'], 0)
                    except ProcessLookupError:
                        row['process'] = 'not_running'
                    else:
                        row['process'] = 'pid_exists_not_identity_verified'
            output(rows)
        elif args.command == 'stop':
            store.stop(args.thread)
            output({'stopped': args.thread, 'note': '只取消未来自动续跑；已经发送的消息不会被撤回'})
        elif args.command == '_watch':
            serve_watch(args, store)
        elif args.command == 'start':
            # Validate the exact thread before arming a background process.
            assessment = inspect_task(args.home, args.app, args.thread)
            if not assessment['canMonitor']:
                raise RuntimeError(assessment['reason'])
            if store.uncertain(args.thread):
                raise RuntimeError('此任务有不确定的发送记录；先人工检查，不能自动重新接管')
            if store.count(args.thread) >= args.max_resumes:
                raise RuntimeError('累计尝试次数已达到上限；检查记录后再决定是否提高 --max-resumes')
            store.lock(args.thread)
            store.arm(args.thread, args.max_resumes)
            # Keep the same flock held across process creation. The child adopts
            # this descriptor; closing only the parent's copy cannot unlock it.
            lock_fd = store.lock_file.fileno()
            log = args.state_dir / (args.thread + '.log')
            cmd, child_cwd, child_env = watch_process_spec(args, lock_fd)
            try:
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'ab') as stream:
                    child = subprocess.Popen(cmd, cwd=child_cwd, env=child_env,
                                             stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                             start_new_session=True, pass_fds=(lock_fd,))
            except Exception:
                store.update(args.thread, 'blocked', '后台进程启动失败；未启用托管', True,
                             only_enabled=True)
                raise
            # Verify child liveness and its first ledger update, rather than claim launch from Popen alone.
            for _ in range(40):
                state = store.get(args.thread)
                if state['status'] != 'starting' or child.poll() is not None:
                    break
                time.sleep(0.1)
            if child.poll() is not None:
                if store.get(args.thread)['status'] == 'starting':
                    store.update(args.thread, 'blocked', '后台进程启动失败', True, only_enabled=True)
                raise RuntimeError('后台监控已停止，请用 status 查看原因')
            output({'threadId': args.thread, 'pid': child.pid, 'log': str(log),
                    'status': store.get(args.thread)['status'],
                    'note': '仅所选任务；不会自动批准操作。App 必须保持打开。'})
    finally:
        store.close()


def cli():
    try:
        main()
    except KeyboardInterrupt:
        print('操作已中止', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Known local errors contain no session content. Other exceptions stay generic.
        from .app import AppError
        if isinstance(exc, (AppError, RuntimeError, ValueError)):
            print(str(exc), file=sys.stderr)
        else:
            print('操作失败；请检查 App、目录权限和网络，未自动重试发送。', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    cli()
