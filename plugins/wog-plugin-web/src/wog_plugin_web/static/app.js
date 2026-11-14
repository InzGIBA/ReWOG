/* Serve shell: one Alpine app over the REST API.
 *
 * Hash routing keeps the server down to two page routes (the shell at
 * "/" and "/weapon/<name>", both serving this same document); every
 * state change is a fetch. All rendered content goes through x-text,
 * so game-controlled strings never hit innerHTML. User-visible strings
 * come from the injected WOG_I18N table (en/ru, localStorage-persisted).
 */
'use strict';

document.addEventListener('alpine:init', () => {
  Alpine.data('shell', () => ({
    /* Pipeline stage dots in card order: flag key -> i18n title key */
    FLAGS: [
      ['has_key', 'flagKey'],
      ['downloaded', 'flagDownloaded'],
      ['decrypted', 'flagDecrypted'],
      ['unpacked', 'flagUnpacked'],
      ['scene', 'flagScene'],
    ],

    /* Detail-page stepper: every extraction stage, in run order */
    PIPELINE: [
      ['has_key', 'stepKey'],
      ['downloaded', 'stepDownload'],
      ['decrypted', 'stepDecrypt'],
      ['unpacked', 'stepUnpack'],
      ['scene', 'stepScene'],
      ['previews', 'stepPreviews'],
    ],

    FORMATS: ['stl', 'usd', 'usdz', 'fbx'],

    STATUS_KEYS: {
      queued: 'statusQueued',
      running: 'statusRunning',
      done: 'statusDone',
      failed: 'statusFailed',
      cancelled: 'statusCancelled',
    },

    /* Built-in state labels; plugin states fall back to their raw name */
    STATE_KEYS: {
      assembled: 'stateAssembled',
      'field-strip': 'stateFieldStrip',
      exploded: 'stateExploded',
      parts: 'stateParts',
    },

    token: document.documentElement.dataset.token,
    strings: window.WOG_I18N || { en: {}, ru: {} },
    states: window.WOG_STATES || ['assembled', 'field-strip', 'exploded', 'parts'],
    lang: 'en',
    schema: '',
    weapons: [],
    loaded: false,
    filter: '',
    category: '',
    route: { view: 'catalog', weapon: null, job: null },
    weapon: null,
    form: {
      state: '',
      viewer: false,
      animations: false,
      formats: [],
      video: false,
      updateKeys: false,
      parts: '',
    },
    jobs: [],
    job: null,
    logOpen: false,
    logLines: '',
    logLive: false,
    logJobId: null,
    source: null,

    get base() {
      return `/t/${this.token}`;
    },

    t(key, vars) {
      const table = this.strings[this.lang] || this.strings.en || {};
      let text = table[key] ?? (this.strings.en || {})[key] ?? key;
      if (vars) {
        for (const [name, value] of Object.entries(vars)) {
          text = text.split(`{${name}}`).join(String(value));
        }
      }
      return text;
    },

    initLang() {
      let saved = null;
      try {
        saved = localStorage.getItem('wog.lang');
      } catch (error) {
        /* private mode: fall through to navigator */
      }
      const nav = (navigator.language || 'en').toLowerCase();
      this.lang =
        saved === 'ru' || saved === 'en' ? saved : nav.startsWith('ru') ? 'ru' : 'en';
      document.documentElement.lang = this.lang;
    },

    setLang(lang) {
      this.lang = lang;
      try {
        localStorage.setItem('wog.lang', lang);
      } catch (error) {
        /* private mode: language stays for this page only */
      }
      document.documentElement.lang = lang;
    },

    displayName(w) {
      if (!w) return '';
      if (this.lang === 'ru' && w.display_name_ru) return w.display_name_ru;
      return w.display_name || w.name;
    },

    get weaponBrief() {
      if (!this.weapon) return '';
      return this.lang === 'ru' && this.weapon.brief_info_ru
        ? this.weapon.brief_info_ru
        : this.weapon.brief_info || '';
    },

    get weaponFeatures() {
      if (!this.weapon) return '';
      return this.lang === 'ru' && this.weapon.features_ru
        ? this.weapon.features_ru
        : this.weapon.features || '';
    },

    get pipeline() {
      const stages = this.PIPELINE.map(([key, titleKey]) => ({
        key,
        title: this.t(titleKey),
        done: Boolean(this.weapon && this.weapon[key]),
      }));
      /* The wire into stage i is lit once stage i-1 is done */
      return stages.map((s, i) => ({ ...s, wire: i > 0 && stages[i - 1].done }));
    },

    get stats() {
      const where =
        this.route.view === 'catalog'
          ? `${this.visible.length}/${this.weapons.length}`
          : this.route.weapon || '';
      return this.schema ? `${where} · ${this.schema}` : where;
    },

    get visible() {
      const needle = this.filter.trim().toLowerCase();
      return this.weapons.filter((w) => {
        if (this.category && !(w.categories || []).includes(this.category)) {
          return false;
        }
        if (!needle) return true;
        const haystack = [w.name, w.display_name, w.display_name_ru, ...(w.categories || [])]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(needle);
      });
    },

    /* Distinct category leaves with counts, sorted by frequency */
    get categories() {
      const counts = new Map();
      for (const w of this.weapons) {
        for (const c of w.categories || []) {
          counts.set(c, (counts.get(c) || 0) + 1);
        }
      }
      return [...counts.entries()]
        .map(([name, count]) => ({ name, count }))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
    },

    get activeJobs() {
      return this.jobs.filter((j) => j.status === 'queued' || j.status === 'running');
    },

    get historyJobs() {
      return this.jobs.filter((j) => j.status !== 'queued' && j.status !== 'running');
    },

    statusLabel(status) {
      return this.t(this.STATUS_KEYS[status] || status);
    },

    stateLabel(state) {
      return this.t(this.STATE_KEYS[state] || state);
    },

    statusClass(status) {
      return `st-${status}`;
    },

    jobWeapons(job) {
      const options = (job && job.options) || {};
      const list = options.weapons || (options.weapon ? [options.weapon] : []);
      return list.join(', ');
    },

    fmtTime(iso) {
      if (!iso) return '—';
      return new Date(iso).toLocaleString(this.lang === 'ru' ? 'ru-RU' : 'en-GB');
    },

    toggleFormat(format) {
      const index = this.form.formats.indexOf(format);
      if (index >= 0) this.form.formats.splice(index, 1);
      else this.form.formats.push(format);
    },

    async init() {
      this.initLang();
      /* Deep links to /weapon/<name> (server serves the same shell)
         become hash routes so a refresh keeps the view */
      const path = location.pathname.match(/\/weapon\/([^/]+)\/?$/);
      if (path && !location.hash) {
        history.replaceState(null, '', `${this.base}/#/weapon/${path[1]}`);
      }
      window.addEventListener('hashchange', () => this.navigate());
      document.addEventListener('keydown', (event) => {
        if (event.key !== '/' || event.target.id === 'filter') return;
        event.preventDefault();
        document.getElementById('filter').focus();
      });
      await this.loadCatalog();
      await this.loadJobs();
      this.navigate();
      setInterval(() => this.pollJobs(), 2000);
      /* Resume following whatever is running (or ran last) */
      const active = this.jobs.find((j) => j.status === 'running' || j.status === 'queued');
      if (active) this.listen(active.id);
    },

    parseHash() {
      let match = location.hash.match(/^#\/jobs\/(\d+)\/?$/);
      if (match) return { view: 'job', weapon: null, job: Number(match[1]) };
      if (/^#\/jobs\/?$/.test(location.hash)) {
        return { view: 'jobs', weapon: null, job: null };
      }
      match = location.hash.match(/^#\/weapon\/([^/]+?)(\/view)?\/?$/);
      if (match) {
        return {
          view: match[2] ? 'viewer' : 'weapon',
          weapon: decodeURIComponent(match[1]),
          job: null,
        };
      }
      return { view: 'catalog', weapon: null, job: null };
    },

    async navigate() {
      this.route = this.parseHash();
      if (this.route.weapon) {
        await this.loadWeapon(this.route.weapon);
      } else {
        this.weapon = null;
      }
      if (this.route.view === 'jobs') await this.loadJobs();
      if (this.route.view === 'job') {
        await this.loadJob(this.route.job);
        if (this.job && (this.job.status === 'running' || this.job.status === 'queued')) {
          this.listen(this.job.id);
        }
      }
    },

    async loadCatalog() {
      const response = await fetch(`${this.base}/api/catalog`);
      const payload = await response.json();
      this.weapons = payload.weapons;
      this.schema = payload.schema;
      this.loaded = true;
    },

    async loadWeapon(name) {
      const response = await fetch(`${this.base}/api/weapons/${name}`);
      this.weapon = response.ok ? await response.json() : null;
    },

    async loadJobs() {
      const response = await fetch(`${this.base}/api/jobs`);
      if (response.ok) this.jobs = (await response.json()).jobs;
    },

    async loadJob(id) {
      const response = await fetch(`${this.base}/api/jobs/${id}`);
      this.job = response.ok ? await response.json() : null;
      this.$nextTick(() => {
        const node = this.$refs.joblog;
        if (node) node.scrollTop = node.scrollHeight;
      });
    },

    async pollJobs() {
      const hadActive = this.activeJobs.length > 0;
      await this.loadJobs();
      if (this.route.view === 'job' && this.route.job) {
        const current = this.jobs.find((j) => j.id === this.route.job);
        if (current && (current.status === 'running' || current.status === 'queued')) {
          await this.loadJob(this.route.job);
        }
      }
      if (hadActive && !this.activeJobs.length) {
        this.loadCatalog();
        if (this.route.weapon) this.loadWeapon(this.route.weapon);
        if (this.route.view === 'job' && this.route.job) this.loadJob(this.route.job);
      }
    },

    async cancelJob(id) {
      await fetch(`${this.base}/api/jobs/${id}/cancel`, { method: 'POST' });
      await this.loadJobs();
      if (this.route.view === 'job' && this.route.job === id) await this.loadJob(id);
    },

    cardMeta(w) {
      const bits = [];
      if (w.num_parts) bits.push(this.t('partsCount', { n: w.num_parts }));
      if (w.since) bits.push(String(w.since));
      bits.push(`${(w.size_bytes / 1e6).toFixed(1)} MB`);
      return bits.join(' · ');
    },

    stageBody(stage) {
      const body = { stage };
      const weapon = this.route.weapon;
      if (weapon && stage !== 'download-weapons') body.weapons = [weapon];
      if (stage === 'build-scene') {
        if (this.form.state) body.state = this.form.state;
        body.viewer = this.form.viewer;
        body.animations = this.form.animations;
        body.formats = this.form.formats;
      }
      if (stage === 'download-assets') body.update_keys = this.form.updateKeys;
      if (stage === 'render') body.video = this.form.video;
      if (stage === 'print-export') {
        const parts = this.form.parts
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean);
        if (parts.length) body.parts = parts;
      }
      return body;
    },

    async run(stage) {
      await this.enqueue(this.stageBody(stage));
    },

    async runAll(stage) {
      await this.enqueue({ stage, all: true, video: this.form.video });
    },

    async enqueue(body) {
      const response = await fetch(`${this.base}/api/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      this.logOpen = true;
      if (!response.ok) {
        const detail = (await response.json()).detail || response.statusText;
        this.log(`${this.t('rejected')}: ${detail}`);
        return;
      }
      const started = await response.json();
      await this.loadJobs();
      this.listen(started.job_id);
      this.log(`${this.t('started')}: ${started.args.join(' ')}`);
    },

    /* One SSE stream at a time. The server replays stored lines first
       (resuming via Last-Event-ID on native reconnects), then tails
       live until "done"; switching jobs starts a fresh replay. */
    listen(jobId) {
      if (this.source) {
        this.source.close();
        this.source = null;
      }
      if (jobId !== this.logJobId) {
        this.logJobId = jobId;
        this.logLines = '';
      }
      const url =
        jobId == null
          ? `${this.base}/api/events`
          : `${this.base}/api/events?job_id=${jobId}`;
      const source = new EventSource(url);
      this.source = source;
      this.logLive = true;
      source.onmessage = (event) => this.log(event.data);
      source.addEventListener('done', (event) => {
        source.close();
        if (this.source === source) this.source = null;
        this.logLive = false;
        let payload = {};
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          /* legacy "end" payload */
        }
        if (payload.status && payload.status !== 'idle') {
          this.log(this.t('stageFinished'));
        }
        this.loadJobs();
        this.loadCatalog();
        if (this.route.weapon) this.loadWeapon(this.route.weapon);
        if (this.route.view === 'job' && this.route.job) this.loadJob(this.route.job);
      });
      source.onerror = () => {
        /* EventSource retries transient drops by itself (with
           Last-Event-ID); CLOSED means the token died with a server
           restart, so stop instead of hammering a 404. */
        if (source.readyState === EventSource.CLOSED) {
          if (this.source === source) this.source = null;
          this.logLive = false;
          this.log(this.t('reconnecting'));
        }
      };
    },

    log(text) {
      this.logLines += text + '\n';
      this.$nextTick(() => {
        const node = this.$refs.log;
        if (node) node.scrollTop = node.scrollHeight;
      });
    },
  }));
});