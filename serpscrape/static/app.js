const _FALLBACK_DEFAULTS = {
  per_page_delay_ms: 1500, per_keyword_delay_ms: 5000, max_results: 50,
  engines: ['google'], proxy_server: '', proxy_username: '',
};

function _defaultForm(d) {
  d = d || _FALLBACK_DEFAULTS;
  return {
    name: '',
    keywordsText: '',
    engines: [...(d.engines && d.engines.length ? d.engines : ['google'])],
    country: 'US',
    per_page_delay_ms: d.per_page_delay_ms,
    per_keyword_delay_ms: d.per_keyword_delay_ms,
    max_results: d.max_results,
    notify_email: '',
    useProxy: !!d.proxy_server,
    proxy: { server: d.proxy_server || '', username: d.proxy_username || '', password: '' },
  };
}

function serpApp() {
  return {
    nav: [
      { id: 'new', label: 'New Task', icon: '+' },
      { id: 'history', label: 'History', icon: '#' },
      { id: 'settings', label: 'Settings', icon: '⚙' },
    ],
    route: 'new',
    token: '',
    bootError: '',

    countries: [],
    engines: ['google', 'bing', 'duckduckgo'],

    form: _defaultForm(),
    taskDefaults: { ..._FALLBACK_DEFAULTS },
    submitting: false,
    formError: '',

    activeTasks: [],
    allTasks: [],
    selectedIds: [],
    _pollTimer: null,

    // History view: paging / search / sort / date filter
    historyTotal: 0,
    histPage: 1,
    histPerPage: 10,
    histSearch: '',
    histSort: 'created_at',
    histOrder: 'desc',
    histPeriod: 'all',

    settings: {
      default_notify_email: '', smtp_host: '', smtp_port: 587, smtp_username: '', smtp_password: '',
      smtp_password_set: false, smtp_from: '', smtp_starttls: true, capsolver_api_key: '', capsolver_api_key_set: false,
      default_per_page_delay_ms: 1500, default_per_keyword_delay_ms: 5000, default_max_results: 50,
      default_engines: ['google'], default_proxy_server: '', default_proxy_username: '',
      default_proxy_password: '', default_proxy_password_set: false,
    },
    settingsSaved: false,
    tokens: [],
    newTokenName: '',
    newTokenShown: '',

    resultsTask: null,
    resultGroups: [],
    selectedGroup: null,
    results: [],
    sheetsCopied: false,

    async boot() {
      this._readRoute();
      window.addEventListener('hashchange', () => this._readRoute());
      try {
        const r = await fetch('/api/ui-token');
        if (!r.ok) throw new Error('ui-token ' + r.status);
        const data = await r.json();
        this.token = data.token;
      } catch (e) {
        this.bootError = 'auth bootstrap failed';
        console.error(e);
        return;
      }
      try {
        const [countries] = await Promise.all([
          this.api('GET', '/api/countries'),
        ]);
        // Pin US to the top; the template renders a static (disabled) divider after it.
        const us = countries.find(c => c.code === 'US');
        const rest = countries.filter(c => c.code !== 'US');
        this.countries = us ? [us, ...rest] : countries;
      } catch (e) { console.error(e); }
      // Load user-configured task defaults and seed the New Task form from them.
      try {
        const s = await this.api('GET', '/api/settings');
        this._applyTaskDefaults(s);
        this.form = _defaultForm(this.taskDefaults);
      } catch (e) { console.error(e); }
      await this.refreshActive();
      this._loadForRoute();
      this._pollTimer = setInterval(() => this.refreshActive(), 3000);
    },

    _applyTaskDefaults(s) {
      this.taskDefaults = {
        per_page_delay_ms: s.default_per_page_delay_ms ?? 1500,
        per_keyword_delay_ms: s.default_per_keyword_delay_ms ?? 5000,
        max_results: s.default_max_results ?? 50,
        engines: (s.default_engines && s.default_engines.length) ? s.default_engines : ['google'],
        proxy_server: s.default_proxy_server || '',
        proxy_username: s.default_proxy_username || '',
      };
    },

    _readRoute() {
      const h = (window.location.hash || '').replace(/^#\/?/, '').split('?')[0];
      this.route = ['new', 'history', 'settings'].includes(h) ? h : 'new';
      this._loadForRoute();
    },
    _loadForRoute() {
      if (!this.token) return;  // boot() loads once the token is ready
      if (this.route === 'history') this.loadHistory();
      if (this.route === 'settings') this.loadSettings();
    },

    async api(method, path, body) {
      // Use X-API-Token (not Authorization) so we never override the browser's
      // cached HTTP Basic Auth credentials when running behind an auth_basic proxy.
      const opts = { method, headers: { 'X-API-Token': this.token } };
      if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      if (r.status === 204) return null;
      const text = await r.text();
      const data = text ? JSON.parse(text) : null;
      if (!r.ok) throw new Error((data && data.detail) || ('HTTP ' + r.status));
      return data;
    },

    async refreshAll() {
      await Promise.all([this.refreshActive(), this.loadHistory()]);
    },

    async refreshActive() {
      try {
        // Active = queued + running + paused (for the New Task queue widget).
        const data = await this.api('GET', '/api/tasks?limit=100&offset=0');
        this.activeTasks = data.items.filter(t => ['queued', 'running', 'paused'].includes(t.status));
        // Keep the History view live without disturbing its paging/filters.
        if (this.route === 'history') await this.loadHistory();
      } catch (e) { console.error(e); }
    },

    _periodRange(period) {
      // Returns {after, before} as ISO strings (or null) for the selected period.
      const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
      const now = new Date();
      const today = startOfDay(now);
      if (period === 'today') return { after: today.toISOString(), before: null };
      if (period === 'yesterday') {
        const y = new Date(today); y.setDate(y.getDate() - 1);
        return { after: y.toISOString(), before: today.toISOString() };
      }
      if (period === '7d') {
        const a = new Date(now); a.setDate(a.getDate() - 7);
        return { after: a.toISOString(), before: null };
      }
      if (period === 'month') {
        const a = new Date(now); a.setDate(a.getDate() - 30);
        return { after: a.toISOString(), before: null };
      }
      return { after: null, before: null };
    },

    async loadHistory() {
      try {
        const params = new URLSearchParams();
        params.set('limit', this.histPerPage);
        params.set('offset', (this.histPage - 1) * this.histPerPage);
        params.set('sort', this.histSort);
        params.set('order', this.histOrder);
        if (this.histSearch.trim()) params.set('q', this.histSearch.trim());
        const { after, before } = this._periodRange(this.histPeriod);
        if (after) params.set('created_after', after);
        if (before) params.set('created_before', before);
        const data = await this.api('GET', '/api/tasks?' + params.toString());
        this.allTasks = data.items;
        this.historyTotal = data.total;
        // If the current page is now beyond the result set (e.g. after deletes), step back.
        const pages = Math.max(1, Math.ceil(this.historyTotal / this.histPerPage));
        if (this.histPage > pages) { this.histPage = pages; }
      } catch (e) { console.error(e); }
    },

    get historyPages() {
      return Math.max(1, Math.ceil(this.historyTotal / this.histPerPage));
    },
    applyHistoryFilters() {
      this.histPage = 1;
      this.selectedIds = [];
      this.loadHistory();
    },
    setSort(field) {
      if (this.histSort === field) {
        this.histOrder = this.histOrder === 'asc' ? 'desc' : 'asc';
      } else {
        this.histSort = field;
        this.histOrder = 'desc';
      }
      this.histPage = 1;
      this.loadHistory();
    },
    sortArrow(field) {
      if (this.histSort !== field) return '';
      return this.histOrder === 'asc' ? ' ▲' : ' ▼';
    },
    gotoPage(p) {
      const pages = this.historyPages;
      this.histPage = Math.min(pages, Math.max(1, p));
      this.selectedIds = [];
      this.loadHistory();
    },

    async submitTask() {
      this.formError = '';
      const kws = this.form.keywordsText.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      if (!kws.length) { this.formError = 'At least one keyword'; return; }
      if (!this.form.engines.length) { this.formError = 'Select at least one engine'; return; }
      const payload = {
        name: this.form.name || null,
        keywords: kws,
        engines: this.form.engines,
        country: this.form.country,
        per_page_delay_ms: this.form.per_page_delay_ms,
        per_keyword_delay_ms: this.form.per_keyword_delay_ms,
        max_results: Math.min(100, Math.max(1, this.form.max_results || 100)),
        notify_email: this.form.notify_email || null,
        proxy: null,
      };
      if (this.form.useProxy && this.form.proxy.server) {
        payload.proxy = {
          server: this.form.proxy.server,
          username: this.form.proxy.username || null,
          password: this.form.proxy.password || null,
        };
      }
      this.submitting = true;
      try {
        await this.api('POST', '/api/tasks', payload);
        this.form = _defaultForm(this.taskDefaults);
        await this.refreshAll();
      } catch (e) {
        this.formError = String(e.message || e);
      } finally {
        this.submitting = false;
      }
    },

    async controlTask(t, action) {
      if (action === 'cancel' && !confirm('Cancel task "' + t.name + '"?')) return;
      try {
        await this.api('PATCH', '/api/tasks/' + t.id, { action });
        await this.refreshAll();
      } catch (e) {
        alert('Failed: ' + e.message);
      }
    },

    toggleSelect(id) {
      const i = this.selectedIds.indexOf(id);
      if (i === -1) this.selectedIds.push(id);
      else this.selectedIds.splice(i, 1);
    },
    toggleSelectAll(ev) {
      this.selectedIds = ev.target.checked ? this.allTasks.map(t => t.id) : [];
    },
    async deleteTask(t) {
      if (!confirm('Delete task "' + t.name + '" and all its results? This cannot be undone.')) return;
      try {
        await this.api('DELETE', '/api/tasks/' + t.id);
        this.selectedIds = this.selectedIds.filter(id => id !== t.id);
        if (this.resultsTask && this.resultsTask.id === t.id) this.resultsTask = null;
        await this.refreshAll();
      } catch (e) { alert('Delete failed: ' + e.message); }
    },
    async deleteSelected() {
      const n = this.selectedIds.length;
      if (!n) return;
      if (!confirm('Delete ' + n + ' task(s) and all their results? This cannot be undone.')) return;
      try {
        await this.api('POST', '/api/tasks/bulk-delete', { ids: this.selectedIds });
        this.selectedIds = [];
        await this.refreshAll();
      } catch (e) { alert('Bulk delete failed: ' + e.message); }
    },

    statusClass(s) {
      return {
        queued:    'bg-slate-700 text-slate-200',
        running:   'bg-amber-600/30 text-amber-300 border border-amber-700',
        paused:    'bg-slate-600/40 text-slate-200 border border-slate-500',
        completed: 'bg-emerald-700/30 text-emerald-300 border border-emerald-700',
        canceled:  'bg-slate-700/50 text-slate-300',
        failed:    'bg-rose-700/30 text-rose-300 border border-rose-700',
      }[s] || 'bg-slate-700 text-slate-200';
    },
    progressText(t) {
      const p = t.progress || {};
      if (!p.total) return '—';
      let s = (p.done || 0) + '/' + p.total;
      if (p.current) s += ' · ' + p.current;
      return s;
    },
    progressPct(t) {
      const p = t.progress || {};
      if (!p.total) return 0;
      return Math.min(100, Math.round(((p.done || 0) / p.total) * 100));
    },
    formatTime(iso) {
      if (!iso) return '—';
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    },

    async loadSettings() {
      try {
        this.settings = await this.api('GET', '/api/settings');
        this.tokens = await this.api('GET', '/api/tokens');
        this.settings.smtp_password = '';
        this.settings.capsolver_api_key = '';
        this.settings.default_proxy_password = '';
      } catch (e) { console.error(e); }
    },
    async saveSettings() {
      const payload = {
        default_notify_email: this.settings.default_notify_email || null,
        smtp_host: this.settings.smtp_host || null,
        smtp_port: this.settings.smtp_port || null,
        smtp_username: this.settings.smtp_username || null,
        smtp_from: this.settings.smtp_from || null,
        smtp_starttls: !!this.settings.smtp_starttls,
        default_per_page_delay_ms: this.settings.default_per_page_delay_ms,
        default_per_keyword_delay_ms: this.settings.default_per_keyword_delay_ms,
        default_max_results: Math.min(100, Math.max(1, this.settings.default_max_results || 50)),
        default_engines: this.settings.default_engines || [],
        default_proxy_server: this.settings.default_proxy_server || null,
        default_proxy_username: this.settings.default_proxy_username || null,
      };
      // Secret fields: only send when the user typed something (blank = keep existing).
      if (this.settings.smtp_password) payload.smtp_password = this.settings.smtp_password;
      if (this.settings.capsolver_api_key) payload.capsolver_api_key = this.settings.capsolver_api_key;
      if (this.settings.default_proxy_password) payload.default_proxy_password = this.settings.default_proxy_password;
      try {
        this.settings = await this.api('PUT', '/api/settings', payload);
        this.settings.smtp_password = '';
        this.settings.capsolver_api_key = '';
        this.settings.default_proxy_password = '';
        this._applyTaskDefaults(this.settings);  // reflect new defaults in future New Task forms
        this.settingsSaved = true;
        setTimeout(() => { this.settingsSaved = false; }, 2000);
      } catch (e) { alert('Save failed: ' + e.message); }
    },
    async createToken() {
      const name = this.newTokenName.trim();
      if (!name) return;
      try {
        const created = await this.api('POST', '/api/tokens', { name });
        this.newTokenShown = created.token;
        this.newTokenName = '';
        this.tokens = await this.api('GET', '/api/tokens');
      } catch (e) { alert('Create failed: ' + e.message); }
    },
    async revokeToken(t) {
      if (!confirm('Revoke token "' + t.name + '"?')) return;
      try {
        await this.api('DELETE', '/api/tokens/' + t.id);
        this.tokens = await this.api('GET', '/api/tokens');
      } catch (e) { alert('Revoke failed: ' + e.message); }
    },

    async copyToClipboard(s) {
      try { await navigator.clipboard.writeText(s); } catch { /* ignore */ }
    },

    async openResults(t) {
      this.resultsTask = t;
      this.resultGroups = [];
      this.results = [];
      this.selectedGroup = null;
      try {
        const summary = await this.api('GET', '/api/tasks/' + t.id + '/summary');
        this.resultGroups = summary.groups || [];
        if (this.resultGroups.length) {
          this.selectedGroup = this.resultGroups[0];
          await this.loadResults();
        }
      } catch (e) { console.error(e); }
    },
    async loadResults() {
      if (!this.resultsTask || !this.selectedGroup) return;
      try {
        const q = new URLSearchParams({
          engine: this.selectedGroup.engine,
          keyword: this.selectedGroup.keyword,
          limit: 200,
        });
        const data = await this.api('GET', '/api/tasks/' + this.resultsTask.id + '/results?' + q.toString());
        this.results = data.items;
      } catch (e) { console.error(e); }
    },
    async exportResults(format) {
      if (!this.resultsTask) return;
      // Fetch with the token header, then download the returned blob. A plain
      // <a download> can't send the token, so we go through fetch + object URL.
      try {
        const r = await fetch('/api/tasks/' + this.resultsTask.id + '/export?format=' + format, {
          headers: { 'X-API-Token': this.token },
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const blob = await r.blob();
        const cd = r.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="?([^"]+)"?/);
        const filename = m ? m[1] : ('task_' + this.resultsTask.id + '.' + format);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) { alert('Export failed: ' + e.message); }
    },
    async copyForSheets() {
      if (!this.resultsTask) return;
      try {
        const r = await fetch('/api/tasks/' + this.resultsTask.id + '/export?format=tsv', {
          headers: { 'X-API-Token': this.token },
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const tsv = await r.text();
        await navigator.clipboard.writeText(tsv);
        this.sheetsCopied = true;
        setTimeout(() => { this.sheetsCopied = false; }, 2000);
      } catch (e) { alert('Copy failed: ' + e.message + ' (clipboard needs HTTPS or localhost)'); }
    },
  };
}
