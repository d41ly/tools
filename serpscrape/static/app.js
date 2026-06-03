function _defaultForm() {
  return {
    name: '',
    keywordsText: '',
    engines: ['google'],
    country: 'US',
    per_page_delay_ms: 1500,
    per_keyword_delay_ms: 5000,
    notify_email: '',
    useProxy: false,
    proxy: { server: '', username: '', password: '' },
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
    submitting: false,
    formError: '',

    activeTasks: [],
    allTasks: [],
    _pollTimer: null,

    settings: { default_notify_email: '', smtp_host: '', smtp_port: 587, smtp_username: '', smtp_password: '', smtp_password_set: false, smtp_from: '', smtp_starttls: true },
    settingsSaved: false,
    tokens: [],
    newTokenName: '',
    newTokenShown: '',

    resultsTask: null,
    resultGroups: [],
    selectedGroup: null,
    results: [],

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
        this.countries = countries;
      } catch (e) { console.error(e); }
      await this.refreshAll();
      this._pollTimer = setInterval(() => this.refreshActive(), 3000);
    },

    _readRoute() {
      const h = (window.location.hash || '').replace(/^#\/?/, '').split('?')[0];
      this.route = ['new', 'history', 'settings'].includes(h) ? h : 'new';
      if (this.route === 'history') this.loadHistory();
      if (this.route === 'settings') this.loadSettings();
    },

    async api(method, path, body) {
      const opts = { method, headers: { 'Authorization': 'Bearer ' + this.token } };
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
        // Active = queued + running + paused. Fetch latest 50 of each that aren't terminal.
        const data = await this.api('GET', '/api/tasks?limit=100&offset=0');
        this.activeTasks = data.items.filter(t => ['queued', 'running', 'paused'].includes(t.status));
        // Keep history in sync too if we already loaded it
        if (this.route === 'history') this.allTasks = data.items;
      } catch (e) { console.error(e); }
    },

    async loadHistory() {
      try {
        const data = await this.api('GET', '/api/tasks?limit=200&offset=0');
        this.allTasks = data.items;
      } catch (e) { console.error(e); }
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
        this.form = _defaultForm();
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
      };
      if (this.settings.smtp_password) payload.smtp_password = this.settings.smtp_password;
      try {
        this.settings = await this.api('PUT', '/api/settings', payload);
        this.settings.smtp_password = '';
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
  };
}
