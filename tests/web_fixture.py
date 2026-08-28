"""Explicitly synthetic browser test data; never reads or resumes real App tasks.
Run: python3 tests/web_fixture.py (loopback 8876, token ui-test-only).
"""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_resume.web import APIError, Backend, CompanionServer, Handler

THREAD='11111111-1111-4111-8111-111111111111'
COMPLETE='22222222-2222-4222-8222-222222222222'

class Fixture(Backend):
    def __init__(self, trace=False):
        super().__init__(SimpleNamespace())
        self.rows=[]
        self.trace=trace
    def command(self, action, *args):
        if self.trace:
            import json
            print(json.dumps({'syntheticAction':action}),flush=True)
        if action=='status': return self.rows
        if action=='doctor':
            return {'app':{'version':'UI fixture','build':'synthetic'},'ipc':'connected','ready':False,
                'reason':'模拟数据：等待五小时窗口重置，不连接真实账户',
                'rateLimits':{'limitId':'codex','primary':{'usedPercent':100,'resetsAt':time.time()+1800},
                              'secondary':{'usedPercent':28,'resetsAt':time.time()+86400}}}
        if action=='list': return [{'id':THREAD,'name':'[模拟] 为续跑工具开发 Web 和手机界面','cwd':'/synthetic'},
                                   {'id':COMPLETE,'name':'[模拟] 已完成任务 <script>','cwd':'/synthetic'}]
        if action=='check':
            return {'threadId':args[0],'title':'[模拟] 配套界面开发','decision':'stop' if args[0]==COMPLETE else 'wait',
                    'taskState':'idle' if args[0]==COMPLETE else 'running',
                    'canMonitor':args[0]!=COMPLETE, 'source':'live', 'connection':'connected',
                    'reason':'本轮正常结束；不推断还有工作需要继续' if args[0]==COMPLETE else '任务正在运行，等待观察',
                    'model':'test-model'}
        if action=='start':
            self.rows=[{'thread_id':args[0],'enabled':1,'status':'waiting_quota','reason':'模拟数据：额度耗尽，等待自然重置',
                        'attempts':0,'max_resumes':int(args[2]),'updated':time.time()}]
            return {'threadId':args[0],'status':'waiting_quota'}
        if action=='stop':
            for row in self.rows:
                if row['thread_id']==args[0]: row.update(enabled=0,status='paused',reason='用户已关闭自动续跑',updated=time.time())
            return {'stopped':args[0]}
        raise APIError('不支持的模拟操作')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--mini-app-id')
    parser.add_argument('--trace',action='store_true')
    args=parser.parse_args()
    class TraceHandler(Handler):
        def guard(self, api=False):
            if args.trace and api:
                import json
                print(json.dumps({'syntheticHeaders':{k:self.headers.get(k) for k in
                    ('Origin','Referer','Sec-Fetch-Site')}},ensure_ascii=False),flush=True)
            return super().guard(api)
    with CompanionServer(('127.0.0.1',8876),Fixture(trace=args.trace),token='ui-test-only',mini_app_id=args.mini_app_id) as server:
        server.RequestHandlerClass=TraceHandler
        print('Synthetic UI only: http://127.0.0.1:8876/ ; token ui-test-only',flush=True)
        server.serve_forever()
