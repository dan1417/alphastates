/**
 * Strategy Analysis Page App
 * Alpine.js component for analyzing delegators of a proxy address.
 */

function strategyApp() {
    return {
        // Mix in address modal helpers
        ...addressModalHelpers,

        // State
        proxyAddress: '',
        delegators: [],
        loading: true,
        error: null,
        labelRefreshKey: 0,

        // Stake summary state
        stakeRows: [],
        totalsByNetuid: {},
        grandTotalTao: 0,
        sortedNetuids: [],
        stakeLoading: false,
        stakeError: null,

        // Display + sort controls for stake distribution table
        // displayMode: 'percent' | 'tao'
        // sortColumn: 'total' | <netuid string>
        // sortDir:    'asc' | 'desc'
        displayMode: 'percent',
        sortColumn: 'total',
        sortDir: 'desc',

        async init() {
            const pathParts = window.location.pathname.split('/');
            this.proxyAddress = pathParts[pathParts.length - 1];

            if (!this.proxyAddress || this.proxyAddress.length < 10) {
                this.error = 'Invalid address';
                this.loading = false;
                return;
            }

            const addressLabel = addressLabels.getLabel(this.proxyAddress);
            document.title = `Strategy: ${addressLabel || this.formatAddress(this.proxyAddress)} | AlphaStats`;

            await this.loadDelegators();

            // Kick off stake aggregation if any delegators were found.
            if (this.delegators.length > 0) {
                this.loadStakeSummary();
            }
        },

        refreshDataForLabelUpdate() {
            this.labelRefreshKey++;
        },

        getAddressLabel(address) {
            const _ = this.labelRefreshKey;
            return addressLabels.getLabel(address);
        },

        /**
         * Load all delegators that added the proxy address as a proxy
         */
        async loadDelegators() {
            this.loading = true;
            this.error = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }

                const response = await fetch(
                    `/api/v1/proxy/${this.proxyAddress}`,
                    {
                        headers: {
                            'Authorization': `Bearer ${token}`
                        }
                    }
                );

                if (!response.ok) {
                    if (response.status === 401) {
                        localStorage.removeItem('auth_token');
                        window.location.href = '/';
                        return;
                    }
                    throw new Error(`Failed to load delegators: ${response.statusText}`);
                }

                const data = await response.json();
                this.delegators = data.incoming || [];
            } catch (err) {
                console.error('Error loading delegators:', err);
                this.error = err.message || 'Failed to load delegators';
            } finally {
                this.loading = false;
            }
        },

        /**
         * Load stake summary across all delegators for this proxy address.
         * Hits the chain via the API and can take a while if there are many
         * delegators, so it runs separately from the initial delegator load.
         */
        async loadStakeSummary() {
            this.stakeLoading = true;
            this.stakeError = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }

                const response = await fetch(
                    `/api/v1/proxy/${this.proxyAddress}/stake-summary`,
                    {
                        headers: {
                            'Authorization': `Bearer ${token}`
                        }
                    }
                );

                if (!response.ok) {
                    if (response.status === 401) {
                        localStorage.removeItem('auth_token');
                        window.location.href = '/';
                        return;
                    }
                    throw new Error(`Failed to load stake summary: ${response.statusText}`);
                }

                const data = await response.json();
                this.stakeRows = data.delegators || [];
                this.totalsByNetuid = data.totals_by_subnet || {};
                this.grandTotalTao = data.grand_total_tao || 0;

                // Column order is fixed by aggregate τ across delegators (desc).
                // Row sort is dynamic via sortedStakeRows below.
                this.sortedNetuids = Object.keys(this.totalsByNetuid)
                    .sort((a, b) =>
                        (this.totalsByNetuid[b].tao || 0) - (this.totalsByNetuid[a].tao || 0)
                    );

                // Reset to default sort each time fresh data lands.
                this.sortColumn = 'total';
                this.sortDir = 'desc';
            } catch (err) {
                console.error('Error loading stake summary:', err);
                this.stakeError = err.message || 'Failed to load stake summary';
            } finally {
                this.stakeLoading = false;
            }
        },

        /**
         * Click handler for a sortable column header.
         * Same column flips direction; new column starts at desc.
         */
        sortBy(column) {
            if (this.sortColumn === column) {
                this.sortDir = this.sortDir === 'desc' ? 'asc' : 'desc';
            } else {
                this.sortColumn = column;
                this.sortDir = 'desc';
            }
        },

        /**
         * Sort indicator glyph for a column header.
         */
        sortIndicator(column) {
            if (this.sortColumn !== column) return '';
            return this.sortDir === 'desc' ? ' ▼' : ' ▲';
        },

        /**
         * Raw τ value for (row, netuid). Used by both the sort key and the
         * displayed cell in τ mode.
         */
        cellTao(row, netuid) {
            const v = row.stakes_by_subnet ? row.stakes_by_subnet[netuid] : null;
            return v ? (v.tao || 0) : 0;
        },

        /**
         * Fractional share (0..1) for (row, netuid). Used by both the sort
         * key and the displayed cell in % mode.
         */
        cellPercent(row, netuid) {
            const tao = this.cellTao(row, netuid);
            if (!row.total_tao || row.total_tao <= 0) return 0;
            return tao / row.total_tao;
        },

        /**
         * Value to render in a (row, netuid) cell based on the current mode.
         */
        cellDisplay(row, netuid) {
            const tao = this.cellTao(row, netuid);
            if (this.displayMode === 'tao') {
                return tao > 0 ? this.formatTao(tao) + ' τ' : '—';
            }
            return this.formatPercent(tao, row.total_tao);
        },

        /**
         * Value to render in the totals row for a subnet column.
         * In % mode, the percentage is against the grand total τ.
         * In τ mode, just show the aggregate τ for that subnet.
         */
        totalsCellDisplay(netuid) {
            const tao = this.totalsByNetuid[netuid] ? (this.totalsByNetuid[netuid].tao || 0) : 0;
            if (this.displayMode === 'tao') {
                return tao > 0 ? this.formatTao(tao) + ' τ' : '—';
            }
            return this.formatPercent(tao, this.grandTotalTao);
        },

        /**
         * Stake rows sorted by the active column + direction. The totals row
         * is rendered separately so it always pins to the top regardless of
         * sort.
         */
        get sortedStakeRows() {
            const rows = [...this.stakeRows];
            const dir = this.sortDir === 'desc' ? -1 : 1;
            const col = this.sortColumn;

            let keyFn;
            if (col === 'total') {
                keyFn = (r) => r.total_tao || 0;
            } else {
                // Subnet column. In % mode sort by share, in τ mode by raw τ.
                if (this.displayMode === 'percent') {
                    keyFn = (r) => this.cellPercent(r, col);
                } else {
                    keyFn = (r) => this.cellTao(r, col);
                }
            }

            rows.sort((a, b) => {
                const av = keyFn(a);
                const bv = keyFn(b);
                if (av === bv) return 0;
                return av < bv ? -1 * dir : 1 * dir;
            });
            return rows;
        },

        setDisplayMode(mode) {
            this.displayMode = mode;
        },

        formatTao(value) {
            if (!value) return '0.00';
            if (value >= 1000) return value.toFixed(0);
            return value.toFixed(2);
        },

        formatPercent(value, total) {
            if (!total || total <= 0) return '—';
            const pct = (value / total) * 100;
            if (pct === 0) return '—';
            if (pct < 0.01) return '<0.01%';
            return pct.toFixed(2) + '%';
        },

        formatAddress(address) {
            if (!address) return '';
            if (address.length > 20) {
                return `${address.substring(0, 10)}...${address.substring(address.length - 8)}`;
            }
            return address;
        },

        async copyAddress(address) {
            try {
                await navigator.clipboard.writeText(address);
                const event = new CustomEvent('show-toast', {
                    detail: { message: 'Address copied to clipboard!', type: 'success' }
                });
                window.dispatchEvent(event);
            } catch (error) {
                console.error('Failed to copy address:', error);
            }
        }
    };
}
