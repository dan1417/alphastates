/**
 * All Subnets page — sortable table fed by a live WebSocket snapshot.
 */

function subnetsApp() {
    return {
        ...addressModalHelpers,

        subnets: [],
        blockNumber: null,
        lockCost: null,
        liveConnected: false,
        showUtc: true,

        sortColumn: 'netuid',
        sortDirection: 'asc',

        filterNetuid: '',
        filterName: '',

        ws: null,
        _wsReconnectTimer: null,
        _wsPingTimer: null,

        async init() {
            this.connectWebSocket();
            window.addEventListener('beforeunload', () => this.disconnectWebSocket());
            // Keep the table head's sticky offset in sync with the card-header height.
            this.$nextTick(() => this._observeCardHeader());
        },

        _observeCardHeader() {
            const el = this.$refs.cardHeader;
            if (!el) return;
            const update = () => {
                document.documentElement.style.setProperty(
                    '--subnets-card-header-h',
                    el.offsetHeight + 'px',
                );
            };
            update();
            if (typeof ResizeObserver !== 'undefined') {
                new ResizeObserver(update).observe(el);
            } else {
                window.addEventListener('resize', update);
            }
        },

        get filterNameError() {
            const pattern = this.filterName.trim();
            if (!pattern) return '';
            try {
                new RegExp(pattern, 'i');
                return '';
            } catch (err) {
                return 'Invalid regex: ' + err.message;
            }
        },

        get filteredSubnets() {
            const netuidStr = this.filterNetuid.trim();
            const namePattern = this.filterName.trim();

            let nameRegex = null;
            if (namePattern) {
                try {
                    nameRegex = new RegExp(namePattern, 'i');
                } catch (err) {
                    nameRegex = null;
                }
            }

            return this.subnets.filter(s => {
                if (netuidStr && String(s.netuid) !== netuidStr) return false;
                if (nameRegex && !nameRegex.test(s.name || '')) return false;
                return true;
            });
        },

        get sortedSubnets() {
            const arr = [...this.filteredSubnets];
            const col = this.sortColumn;
            const dir = this.sortDirection === 'asc' ? 1 : -1;

            arr.sort((a, b) => {
                const av = a[col];
                const bv = b[col];

                // Null/undefined sort last regardless of direction
                if (av == null && bv == null) return 0;
                if (av == null) return 1;
                if (bv == null) return -1;

                if (typeof av === 'number' && typeof bv === 'number') {
                    return (av - bv) * dir;
                }
                return String(av).localeCompare(String(bv)) * dir;
            });
            return arr;
        },

        sortBy(column) {
            if (this.sortColumn === column) {
                this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortColumn = column;
                // Sensible defaults: numbers desc (biggest first), strings asc
                const descDefaultCols = [
                    'price', 'liq_price', 'adr', 'ema_price',
                    'tao_in', 'alpha_in', 'alpha_out',
                    'total_emission', 'chain_buy',
                    'registered_at', 'netuid',
                    'is_immune', 'is_at_risk',
                ];
                this.sortDirection = descDefaultCols.includes(column) ? 'desc' : 'asc';
            }
        },

        indicator(column) {
            if (this.sortColumn !== column) return '';
            return this.sortDirection === 'asc' ? ' ↑' : ' ↓';
        },

        adrClass(s) {
            if (s.netuid === 0 || s.adr == null) return 'meta-value';
            return Number(s.adr) <= 1 ? 'adr-up' : 'adr-down';
        },

        formatAmount(value) {
            const n = Number(value);
            if (!isFinite(n)) return '—';
            const abs = Math.abs(n);
            if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
            if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K';
            return n.toFixed(2);
        },

        formatUtc(ts) {
            if (ts == null) return '—';
            const d = new Date(Number(ts) * 1000);
            if (isNaN(d.getTime())) return '—';
            const pad = (n) => String(n).padStart(2, '0');
            return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
        },

        formatLocal(ts) {
            if (ts == null) return '—';
            const d = new Date(Number(ts) * 1000);
            if (isNaN(d.getTime())) return '—';
            const pad = (n) => String(n).padStart(2, '0');
            const tz = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
                .formatToParts(d)
                .find(p => p.type === 'timeZoneName')?.value || '';
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())} ${tz}`;
        },

        formatRegisteredTs(ts) {
            return this.showUtc ? this.formatUtc(ts) : this.formatLocal(ts);
        },

        _normalizeSubnets(subnetsDict) {
            // Snapshot keys are strings (JSON object keys); values already shaped by the cacher.
            // netuid 0 is the root subnet — it has no meaningful ema_price and is never at risk.
            const arr = Object.values(subnetsDict || {}).map(s => {
                const price = Number(s.price);
                const liq = Number(s.liq_price);
                return {
                    ...s,
                    name: s.subnet_identity?.subnet_name || '',
                    adr: price > 0 ? price / liq : null,
                    // Strip ema_price on the root subnet so it both displays as — and sorts last.
                    ema_price: s.netuid === 0 ? null : s.ema_price,
                    is_at_risk: false,
                };
            });

            // "At risk" = the 3 non-immune, non-root subnets with the lowest ema_price.
            const candidates = arr
                .filter(s => s.netuid !== 0 && !s.is_immune && s.ema_price != null)
                .sort((a, b) => Number(a.ema_price) - Number(b.ema_price))
                .slice(0, 3);
            const riskSet = new Set(candidates.map(s => s.netuid));
            for (const s of arr) {
                s.is_at_risk = riskSet.has(s.netuid);
            }
            return arr;
        },

        connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            let wsUrl = `${protocol}//${window.location.host}/api/v1/subnets/ws`;
            const token = localStorage.getItem('auth_token');
            if (token) {
                wsUrl += '?token=' + encodeURIComponent(token);
            }

            try {
                this.ws = new WebSocket(wsUrl);
            } catch (err) {
                console.error('Failed to create subnets WebSocket:', err);
                this._scheduleReconnect();
                return;
            }

            this.ws.onopen = () => {
                console.log('✅ All-subnets WS connected');
                this.liveConnected = true;
                this._wsPingTimer = setInterval(() => {
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send('ping');
                    }
                }, 30000);
            };

            this.ws.onmessage = (event) => {
                if (event.data === 'pong') return;
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'all_subnets_snapshot') {
                        this.subnets = this._normalizeSubnets(msg.subnets);
                        this.blockNumber = msg.block_number;
                        this.lockCost = msg.lock_cost ?? null;
                    }
                } catch (err) {
                    console.error('Subnets WS message parse error:', err);
                }
            };

            this.ws.onerror = (err) => {
                console.error('Subnets WS error:', err);
            };

            this.ws.onclose = () => {
                console.log('❌ All-subnets WS disconnected');
                this.liveConnected = false;
                this.ws = null;
                if (this._wsPingTimer) {
                    clearInterval(this._wsPingTimer);
                    this._wsPingTimer = null;
                }
                this._scheduleReconnect();
            };
        },

        _scheduleReconnect() {
            if (this._wsReconnectTimer) return;
            this._wsReconnectTimer = setTimeout(() => {
                this._wsReconnectTimer = null;
                this.connectWebSocket();
            }, 5000);
        },

        disconnectWebSocket() {
            if (this._wsReconnectTimer) {
                clearTimeout(this._wsReconnectTimer);
                this._wsReconnectTimer = null;
            }
            if (this._wsPingTimer) {
                clearInterval(this._wsPingTimer);
                this._wsPingTimer = null;
            }
            if (this.ws) {
                try { this.ws.close(); } catch (e) {}
                this.ws = null;
            }
            this.liveConnected = false;
        },
    };
}
