/* Serve shell: one Alpine app over the REST API.
 *
 * Hash routing keeps the server down to two page routes (the shell at
 * "/" and "/weapon/<name>", both serving this same document); every
 * state change is a fetch. All rendered content goes through x-text,
 * so game-controlled strings never hit innerHTML.
 */
'use strict';

document.addEventListener('alpine:init', () => {
  Alpine.data('shell', () => ({
    /* Pipeline stage dots in card order, with tooltips */
    FLAGS: [
      ['has_key', 'key'],
      ['downloaded', 'downloaded'],
      ['decrypted', 'decrypted'],
      ['unpacked', 'unpacked'],
      ['scene', 'scene'],
    ],

    /* Detail-page stepper: every extraction stage, in run order */
    PIPELINE: [
      ['has_key', 'key'],
      ['downloaded', 'dl'],
      ['decrypted', 'dec'],
      ['unpacked', 'unp'],
      ['scene', 'scene'],
      ['previews', 'renders'],
    ],

    get pipeline() {
      const stages = this.PIPELINE.map(([key, title]) => ({
        key,
        title,
        done: Boolean(this.weapon && this.weapon[key]),
      }));
      /* The wire into stage i is lit once stage i-1 is done */
      return stages.map((s, i) => ({ ...s, wire: i > 0 && stages[i - 1].done }));
    },

    token: document.documentElement.dataset.token,
    schema: '',
    weapons: [],
    loaded: false,
    filter: '',
    category: '',
    route: { view: 'catalog', weapon: null },
    weapon: null,
    state: '',
    logOpen: false,
    logLines: '',
    logLive: false,
    source: null,

    get base() {
      return `/t/${this.token}`;
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
        const haystack = [w.name, w.display_name, ...(w.categories || [])]
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

    async init() {
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
      this.navigate();
    },

    parseHash() {
      const match = location.hash.match(/^#\/weapon\/([^/]+?)(\/view)?\/?$/);
      if (match) {
        return {
          view: match[2] ? 'viewer' : 'weapon',
          weapon: decodeURIComponent(match[1]),
        };
      }
      return { view: 'catalog', weapon: null };
    },

    async navigate() {
      this.route = this.parseHash();
      if (this.route.weapon) {
        await this.loadWeapon(this.route.weapon);
      } else {
        this.weapon = null;
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

    cardMeta(w) {
      const bits = [];
      if (w.num_parts) bits.push(`${w.num_parts} parts`);
      if (w.since) bits.push(String(w.since));
      bits.push(`${(w.size_bytes / 1e6).toFixed(1)} MB`);
      return bits.join(' · ');
    },

    async run(stage) {
      const body = { stage };
      if (this.route.weapon) body.weapon = this.route.weapon;
      if (stage === 'build-scene' && this.state) body.state = this.state;
      const response = await fetch(`${this.base}/api/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      this.logOpen = true;
      if (!response.ok) {
        const detail = (await response.json()).detail || response.statusText;
        this.log(`rejected: ${detail}`);
        return;
      }
      const started = await response.json();
      this.log(`started: ${started.args.join(' ')}`);
      this.listen();
    },

    /* One SSE stream at a time; "done" refreshes whatever is on screen */
    listen() {
      if (this.source) return;
      this.source = new EventSource(`${this.base}/api/events`);
      this.logLive = true;
      this.source.onmessage = (event) => this.log(event.data);
      this.source.addEventListener('done', () => {
        this.source.close();
        this.source = null;
        this.logLive = false;
        this.log('stage finished');
        this.loadCatalog();
        if (this.route.weapon) this.loadWeapon(this.route.weapon);
      });
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
