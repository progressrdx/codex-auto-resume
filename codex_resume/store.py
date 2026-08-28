"""Private local ledger; an uncertain dispatch is never automatically repeated."""
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.directory.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise RuntimeError('状态目录必须属于当前用户，且只有当前用户可访问')
        path = self.directory / 'state.sqlite3'
        if path.is_symlink():
            raise RuntimeError('拒绝使用符号链接状态数据库')
        self.db = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS watches (
          thread_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
          max_resumes INTEGER NOT NULL, status TEXT NOT NULL,
          reason TEXT NOT NULL, pid INTEGER, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempts (
          thread_id TEXT NOT NULL, failed_turn TEXT NOT NULL,
          message_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
          state TEXT NOT NULL, resumed_turn TEXT, created REAL NOT NULL,
          PRIMARY KEY(thread_id, failed_turn)
        );
        ''')
        self.lock_file = None

    def close(self):
        if self.lock_file:
            self.lock_file.close()
        self.db.close()

    def lock(self, thread_id, inherited_fd=None):
        path = self.directory / (str(uuid.UUID(thread_id)) + '.lock')
        if inherited_fd is None:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        else:
            fd = inherited_fd
            expected, actual = path.lstat(), os.fstat(fd)
            if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
                    or (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino)
                    or actual.st_uid != os.getuid() or actual.st_mode & 0o077):
                raise RuntimeError('继承的进程锁不是所选任务的私有锁文件')
            os.set_inheritable(fd, False)
        f = os.fdopen(fd, 'w')
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise RuntimeError('该任务已有一个监控进程')
        self.lock_file = f

    def arm(self, thread_id, maximum):
        with self.db:
            self.db.execute('''INSERT INTO watches VALUES (?,1,?,'starting','等待启动',NULL,?)
                ON CONFLICT(thread_id) DO UPDATE SET enabled=1,max_resumes=excluded.max_resumes,
                status='starting',reason='等待启动',pid=NULL,updated=excluded.updated''',
                (thread_id, maximum, time.time()))

    def get(self, thread_id):
        row = self.db.execute('SELECT * FROM watches WHERE thread_id=?', (thread_id,)).fetchone()
        return dict(row) if row else None

    def all(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM watches ORDER BY updated DESC')]

    def update(self, thread_id, status, reason, disable=False, only_enabled=False):
        with self.db:
            self.db.execute('''UPDATE watches SET status=?,reason=?,pid=?,updated=?,
                enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE thread_id=?
                AND (?=0 OR enabled=1)''',
                (status, reason, os.getpid(), time.time(), int(disable), thread_id, int(only_enabled)))

    def stop(self, thread_id):
        self.update(thread_id, 'paused', '用户已关闭自动续跑', True)

    def attempt(self, thread_id, turn_id):
        row = self.db.execute('SELECT * FROM attempts WHERE thread_id=? AND failed_turn=?',
                              (thread_id, turn_id)).fetchone()
        return dict(row) if row else None

    def count(self, thread_id):
        return self.db.execute('SELECT count(*) FROM attempts WHERE thread_id=?', (thread_id,)).fetchone()[0]

    def uncertain(self, thread_id):
        return self.db.execute("SELECT 1 FROM attempts WHERE thread_id=? AND state='dispatching' LIMIT 1", (thread_id,)).fetchone() is not None

    def claim(self, thread_id, turn_id, baseline):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            watch = self.get(thread_id)
            if not watch or not watch['enabled'] or self.count(thread_id) >= watch['max_resumes'] or self.attempt(thread_id, turn_id):
                self.db.rollback()
                return None
            message = str(uuid.uuid4())
            self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,\'dispatching\',NULL,?)',
                            (thread_id, turn_id, message, baseline, time.time()))
            self.db.commit()
            return message
        except Exception:
            self.db.rollback()
            raise

    def acknowledged(self, thread_id, turn_id, resumed_turn):
        with self.db:
            self.db.execute("UPDATE attempts SET state='sent',resumed_turn=? WHERE thread_id=? AND failed_turn=?",
                            (resumed_turn, thread_id, turn_id))

    def can_dispatch(self, thread_id, turn_id, message_id):
        """Final local cancellation/budget check for this exact persisted intent.

        This read is deliberately next to the adapter's network write, after its
        potentially slow fresh snapshot. It cannot make the App write atomic.
        """
        row = self.db.execute('''SELECT 1 FROM watches w JOIN attempts a
            ON a.thread_id=w.thread_id WHERE w.thread_id=? AND w.enabled=1
            AND a.failed_turn=? AND a.message_id=? AND a.state='dispatching'
            AND (SELECT count(*) FROM attempts WHERE thread_id=w.thread_id)<=w.max_resumes''',
            (thread_id, turn_id, message_id)).fetchone()
        return row is not None
