const {createClient, eligible} = require('../../lib/api');
const {stateName, quotaView, watchesView} = require('../../lib/presentation');
Page({
  data: {connected:false, base:'', credential:'', view:'overview', loading:false, checking:false, writing:false,
    error:'', notice:'', threads:[], filtered:[], watches:[], query:'', archived:false, selected:null,
    assessment:null, canStart:false, consent:false, maximum:'3', primary:{remaining:'—'}, secondary:{remaining:'—'},
    pending:null, updated:'', visibleCount:0, connectionReason:''},
  onLoad() {
    this.epoch = 0; this.selection = 0; this.refreshVersion = 0; this.visible = true;
    this.setData({base:wx.getStorageSync('relay-server-address') || ''});
  },
  onShow() { this.visible = true; if (this.client) this.refresh(); this.poll(); },
  onHide() { this.visible = false; clearTimeout(this.timer); this.selection++; this.setData({canStart:false,checking:false}); },
  onUnload() { this.disconnect(); },
  onPullDownRefresh() { Promise.resolve(this.client ? this.refresh(true) : null).finally(() => wx.stopPullDownRefresh()); },
  poll() {
    clearTimeout(this.timer);
    if (!this.visible || !this.client) return;
    this.timer = setTimeout(async () => { await this.refresh(); this.poll(); },15000);
  },
  inputBase(e) { this.setData({base:e.detail.value}); },
  inputCredential(e) { this.setData({credential:e.detail.value}); },
  async connect() {
    if (this.data.loading) return;
    const epoch = ++this.epoch;
    this.setData({loading:true,error:'',notice:''});
    let candidate;
    try {
      const platform = wx.getDeviceInfo ? wx.getDeviceInfo().platform : wx.getSystemInfoSync().platform;
      candidate = createClient(wx,this.data.base,this.data.credential,{devtools:platform === 'devtools'});
      const values = await Promise.all([candidate.threads(),candidate.watches(),candidate.quota()]);
      if (epoch !== this.epoch) { candidate.close(); return; }
      this.client = candidate; getApp().client = candidate;
      wx.setStorageSync('relay-server-address',candidate.origin);
      this.setData({connected:true,credential:'',view:'overview',base:candidate.origin});
      this.applySnapshot(values[0],values[1],values[2]); this.poll();
    } catch (error) { if (candidate) candidate.close(); if (epoch === this.epoch) { this.client = null; getApp().client = null; this.setData({connected:false,error:error.message}); } }
    finally { if (epoch === this.epoch) this.setData({loading:false}); }
  },
  disconnect() {
    if (this.data.writing) return;
    clearTimeout(this.timer); this.epoch++; this.selection++; this.refreshVersion++;
    if (this.client) this.client.close(); this.client = null; getApp().client = null;
    this.setData({connected:false,credential:'',threads:[],filtered:[],watches:[],selected:null,assessment:null,
      canStart:false,loading:false,checking:false,error:'',notice:'',pending:null,view:'overview'});
  },
  applySnapshot(threads,watches,quota) {
    this.setData({threads,watches:watchesView(watches,threads),primary:quotaView(quota.primary),secondary:quotaView(quota.secondary),
      connectionReason:quota.reason || '',pending:this.client.pending(),updated:new Date().toLocaleTimeString()});
    this.filter();
  },
  async refresh(full) {
    if (!this.client) return;
    if (this.refreshing) {
      if (full === true) { await this.refreshing; return this.refresh(true); }
      return this.refreshing;
    }
    let finish; this.refreshing = new Promise(resolve => { finish = resolve; });
    const epoch = this.epoch, version = ++this.refreshVersion, client = this.client;
    try {
      const values = await Promise.all([full === true ? client.threads() : Promise.resolve(this.data.threads),client.watches(),client.quota()]);
      if (epoch !== this.epoch || version !== this.refreshVersion) return;
      this.applySnapshot(...values);
    } catch (error) {
      if (epoch === this.epoch && version === this.refreshVersion) { this.setData({error:error.message}); if (error.status === 401) { this.disconnect(); this.setData({error:'凭据已失效，请重新连接。'}); } }
    } finally { this.refreshing = null; finish(); }
  },
  refreshAll() { return this.refresh(true); },
  showList() { if (this.data.writing) return; this.selection++; this.setData({view:'list',selected:null,assessment:null,canStart:false,error:'',checking:false}); this.filter(); },
  showOverview() { if (this.data.writing) return; this.selection++; this.setData({view:'overview',canStart:false,error:'',checking:false}); this.refresh(); },
  query(e) { this.setData({query:e.detail.value}); this.filter(); },
  toggleArchived(e) { this.setData({archived:e.detail.value}); this.filter(); },
  filter() {
    const query = this.data.query.trim().toLowerCase();
    const filtered = this.data.threads.filter(r => (this.data.archived || !r.archived) && r.title.toLowerCase().includes(query));
    this.setData({filtered,visibleCount:filtered.length});
  },
  async select(e) {
    if (this.data.writing || !this.client) return;
    const row = this.data.threads.find(r => r.id === e.currentTarget.dataset.id);
    if (!row) return;
    const serial = ++this.selection, epoch = this.epoch;
    this.setData({view:'detail',selected:row,assessment:null,canStart:false,consent:false,checking:true,error:'',notice:''});
    try {
      const result = await this.client.check(row.id);
      if (serial !== this.selection || epoch !== this.epoch || !this.visible) return;
      this.setData({assessment:Object.assign({},result,{stateLabel:stateName(result.taskState)}),canStart:!row.archived && eligible(result)});
    } catch (error) { if (serial === this.selection && epoch === this.epoch) this.setData({error:error.message}); }
    finally { if (serial === this.selection && epoch === this.epoch) this.setData({checking:false}); }
  },
  recheck() { if (this.data.selected) return this.select({currentTarget:{dataset:{id:this.data.selected.id}}}); },
  consent(e) { this.setData({consent:e.detail.value.includes('yes')}); },
  maximum(e) { this.setData({maximum:e.detail.value}); },
  async start() {
    if (!this.client || this.data.writing || !this.data.canStart || !this.data.consent || this.client.pending()) return;
    const target = this.data.selected.id;
    const maximum = Number(this.data.maximum);
    if (!Number.isInteger(maximum) || maximum < 1 || maximum > 100) { this.setData({error:'累计续跑上限应为 1–100 的整数。'}); return; }
    const client = this.client, epoch = this.epoch;
    this.setData({writing:true,canStart:false,error:'',notice:''});
    try {
      // Recheck immediately before mutation. Backend also checks independently.
      const current = await client.check(target);
      if (epoch !== this.epoch || !this.visible) return;
      if (!eligible(current)) throw new Error('任务状态已变化，不再适合托管。');
      await client.start(target,maximum,true);
      if (epoch === this.epoch) this.setData({view:'overview',notice:'后端已接受开启请求。请查看下面的实际监控状态。'});
    } catch (error) { if (epoch === this.epoch) this.setData({error:error.message}); }
    finally { if (epoch === this.epoch) { this.setData({writing:false,pending:client.pending()}); await this.refresh(true); } }
  },
  async stop(e) {
    if (!this.client || this.data.writing || this.client.pending()) return;
    const target = e.currentTarget.dataset.id;
    if (!this.data.watches.some(r => r.thread_id === target && r.enabled)) return;
    const epoch = this.epoch, client = this.client;
    this.setData({writing:true,error:'',notice:''});
    try {
      const confirmed = await new Promise(resolve => wx.showModal({title:'停止这个任务的托管？',content:'只停止未来的自动续跑，不中断 App 正在执行的任务，也不能撤回已发送的消息。',confirmText:'停止托管',success:r=>resolve(r.confirm),fail:()=>resolve(false)}));
      if (!confirmed || epoch !== this.epoch || !this.visible) return;
      await client.stop(target);
      if (epoch === this.epoch) this.setData({notice:'后端已接受停止请求。'});
    } catch (error) { if (epoch === this.epoch) this.setData({error:error.message}); }
    finally { if (epoch === this.epoch) { this.setData({writing:false,pending:client.pending()}); await this.refresh(true); } }
  },
  async acknowledge() {
    if (!this.client || this.data.writing) return;
    const client = this.client, epoch = this.epoch;
    const confirmed = await new Promise(resolve => wx.showModal({title:'已经核对监控记录？',content:'解除待确认不会重发请求。请先确认后端实际状态，再决定是否需要新的操作。',confirmText:'已核对',success:r=>resolve(r.confirm),fail:()=>resolve(false)}));
    if (confirmed && epoch === this.epoch) { client.acknowledgePending(); this.setData({pending:null,error:''}); }
  }
});
