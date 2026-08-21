/**
 * Account Page App
 * Alpine.js component for displaying stake information
 */

function accountApp() {
    return {
        // Mix in address modal helpers
        ...addressModalHelpers,
        
        // State
        coldkeyAddress: '',
        stakes: [],
        sortedStakes: [],
        freeTao: 0,
        totalStakedTao: 0,
        loading: true,
        error: null,
        expandedNetuids: new Set(), // Track which netuids are expanded
        labelRefreshKey: 0, // Key to force re-render when labels change
        
        // Trades state
        trades: [],
        tradesLoading: false,
        tradesError: null,
        tradesPage: 1,
        tradesPageSize: 50,
        tradesTotalPages: 1,
        tradesTotalCount: 0,
        tradesPageInput: 1,
        
        // Transfers state
        transfers: [],
        transfersLoading: false,
        transfersError: null,
        transfersPage: 1,
        transfersPageSize: 50,
        transfersTotalPages: 1,
        transfersTotalCount: 0,
        transfersPageInput: 1,

        // Proxy state
        proxiesOutgoing: [],
        proxiesIncoming: [],
        proxiesLoading: false,
        proxiesError: null,

        // Initialize
        async init() {
            // Extract coldkey address from URL
            const pathParts = window.location.pathname.split('/');
            this.coldkeyAddress = pathParts[pathParts.length - 1];
            
            if (!this.coldkeyAddress || this.coldkeyAddress.length < 10) {
                this.error = 'Invalid coldkey address';
                this.loading = false;
                return;
            }

            // Update page title
            const addressLabel = addressLabels.getLabel(this.coldkeyAddress);
            document.title = `${addressLabel || this.formatAddress(this.coldkeyAddress)} | AlphaStats`;

            // Load stake data
            await this.loadStakeInfo();
            
            // Load trades, transfers, and proxies
            await Promise.all([
                this.loadTrades(),
                this.loadTransfers(),
                this.loadProxies()
            ]);
        },

        /**
         * Refresh data after label update (required by addressModalHelpers)
         */
        refreshDataForLabelUpdate() {
            // Increment refresh key to trigger reactivity
            this.labelRefreshKey++;
        },
        
        /**
         * Get address label (reactive wrapper)
         */
        getAddressLabel(address) {
            // Use labelRefreshKey to make this reactive
            const _ = this.labelRefreshKey;
            return addressLabels.getLabel(address);
        },

        /**
         * Load stake information from API
         */
        async loadStakeInfo() {
            this.loading = true;
            this.error = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }
                
                const response = await fetch(
                    `/api/v1/stake/coldkey/${this.coldkeyAddress}`,
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
                    throw new Error(`Failed to load stake info: ${response.statusText}`);
                }

                const data = await response.json();
                
                this.stakes = data.stakes || [];
                this.freeTao = data.free_tao || 0;
                this.totalStakedTao = data.total_staked_tao || 0;
                
                // Sort stakes by amount (descending)
                this.sortedStakes = [...this.stakes].sort((a, b) => b.stake_tao - a.stake_tao);

                console.log('Loaded stake data:', data);
                console.log('Sorted stakes:', this.sortedStakes);
                
                // Force update to trigger computed properties
                this.$nextTick(() => {
                    console.log('Grouped stakes:', this.groupedStakes);
                });

            } catch (err) {
                console.error('Error loading stake info:', err);
                this.error = err.message || 'Failed to load stake information. Please try again later.';
            } finally {
                this.loading = false;
            }
        },


        /**
         * Get number of unique subnets
         */
        get uniqueSubnets() {
            if (!this.stakes || this.stakes.length === 0) return 0;
            const subnets = new Set(this.stakes.map(s => s.netuid));
            return subnets.size;
        },

        /**
         * Get largest stake amount
         */
        get largestStake() {
            if (!this.stakes || this.stakes.length === 0) return 0;
            return Math.max(...this.stakes.map(s => s.stake_tao));
        },

        /**
         * Group stakes by netuid and create flattened array for rendering
         */
        get groupedStakes() {
            if (!this.stakes || this.stakes.length === 0) return [];
            
            // Group stakes by netuid
            const groups = {};
            
            this.stakes.forEach(stake => {
                if (!groups[stake.netuid]) {
                    groups[stake.netuid] = {
                        netuid: stake.netuid,
                        stakes: [],
                        totalStakeTao: 0,
                        totalStakeAlpha: 0
                    };
                }
                
                groups[stake.netuid].stakes.push(stake);
                groups[stake.netuid].totalStakeTao += stake.stake_tao;
                groups[stake.netuid].totalStakeAlpha += stake.stake_alpha;
            });
            
            // Convert to array and sort by total stake (descending)
            const groupArray = Object.values(groups);
            groupArray.sort((a, b) => b.totalStakeTao - a.totalStakeTao);
            
            // Sort stakes within each group by stake amount (descending)
            groupArray.forEach(group => {
                group.stakes.sort((a, b) => b.stake_tao - a.stake_tao);
            });
            
            // Flatten into single array with row type indicators
            const flatRows = [];
            groupArray.forEach(group => {
                // Add group header row
                flatRows.push({
                    rowType: 'group',
                    netuid: group.netuid,
                    totalStakeTao: group.totalStakeTao,
                    totalStakeAlpha: group.totalStakeAlpha,
                    stakesCount: group.stakes.length
                });
                
                // Add all stake rows for this group
                group.stakes.forEach(stake => {
                    flatRows.push({
                        rowType: 'stake',
                        netuid: group.netuid,
                        stake: stake,
                        groupTotalStakeTao: group.totalStakeTao
                    });
                });
            });
            
            return flatRows;
        },

        /**
         * Toggle netuid expansion
         */
        toggleNetuid(netuid) {
            if (this.expandedNetuids.has(netuid)) {
                this.expandedNetuids.delete(netuid);
            } else {
                this.expandedNetuids.add(netuid);
            }
            // Force reactivity by creating a new Set
            this.expandedNetuids = new Set(this.expandedNetuids);
        },

        /**
         * Check if netuid is expanded
         */
        isNetuidExpanded(netuid) {
            return this.expandedNetuids.has(netuid);
        },

        /**
         * Get total TAO (free + staked)
         */
        get totalTao() {
            return this.freeTao + this.totalStakedTao;
        },

        /**
         * Load trades for the address with pagination
         */
        async loadTrades() {
            this.tradesLoading = true;
            this.tradesError = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }
                
                const offset = (this.tradesPage - 1) * this.tradesPageSize;
                const response = await fetch(
                    `/api/v1/trade/all/${this.coldkeyAddress}?limit=${this.tradesPageSize}&offset=${offset}`,
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
                    throw new Error(`Failed to load trades: ${response.statusText}`);
                }

                const data = await response.json();
                
                this.trades = data.trades || [];
                this.tradesTotalCount = data.pagination?.total || 0;
                this.tradesTotalPages = Math.ceil(this.tradesTotalCount / this.tradesPageSize);

                console.log('Loaded trades:', data);
                console.log('Trades array:', this.trades);
                console.log('Total count:', this.tradesTotalCount);

            } catch (err) {
                console.error('Error loading trades:', err);
                this.tradesError = err.message || 'Failed to load trades';
            } finally {
                this.tradesLoading = false;
            }
        },

        /**
         * Load transfers for the address with pagination
         */
        async loadTransfers() {
            this.transfersLoading = true;
            this.transfersError = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }
                
                const offset = (this.transfersPage - 1) * this.transfersPageSize;
                const response = await fetch(
                    `/api/v1/transfer/raw/${this.coldkeyAddress}?limit=${this.transfersPageSize}&offset=${offset}`,
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
                    throw new Error(`Failed to load transfers: ${response.statusText}`);
                }

                const data = await response.json();
                
                this.transfers = data.transfers || [];
                this.transfersTotalCount = data.pagination?.total || 0;
                this.transfersTotalPages = Math.ceil(this.transfersTotalCount / this.transfersPageSize);
                this.transfersPageInput = this.transfersPage;

                console.log('Loaded transfers:', data);

            } catch (err) {
                console.error('Error loading transfers:', err);
                this.transfersError = err.message || 'Failed to load transfers';
            } finally {
                this.transfersLoading = false;
            }
        },

        /**
         * Load proxy info (outgoing + incoming) for the address
         */
        async loadProxies() {
            this.proxiesLoading = true;
            this.proxiesError = null;

            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    window.location.href = '/';
                    return;
                }

                const response = await fetch(
                    `/api/v1/proxy/${this.coldkeyAddress}`,
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
                    throw new Error(`Failed to load proxy info: ${response.statusText}`);
                }

                const data = await response.json();
                this.proxiesOutgoing = data.outgoing || [];
                this.proxiesIncoming = data.incoming || [];
            } catch (err) {
                console.error('Error loading proxy info:', err);
                this.proxiesError = err.message || 'Failed to load proxy info';
            } finally {
                this.proxiesLoading = false;
            }
        },

        /**
         * Go to next page of trades
         */
        nextTradesPage() {
            if (this.tradesPage < this.tradesTotalPages) {
                this.tradesPage++;
                this.tradesPageInput = this.tradesPage;
                this.loadTrades();
            }
        },

        /**
         * Go to previous page of trades
         */
        prevTradesPage() {
            if (this.tradesPage > 1) {
                this.tradesPage--;
                this.tradesPageInput = this.tradesPage;
                this.loadTrades();
            }
        },

        /**
         * Go to specific page of trades
         */
        goToTradesPage() {
            const page = parseInt(this.tradesPageInput);
            if (page >= 1 && page <= this.tradesTotalPages) {
                this.tradesPage = page;
                this.loadTrades();
            } else {
                this.tradesPageInput = this.tradesPage;
            }
        },

        /**
         * Change trades page size
         */
        changeTradesPageSize() {
            this.tradesPage = 1;
            this.tradesPageInput = 1;
            this.loadTrades();
        },

        /**
         * Go to next page of transfers
         */
        nextTransfersPage() {
            if (this.transfersPage < this.transfersTotalPages) {
                this.transfersPage++;
                this.transfersPageInput = this.transfersPage;
                this.loadTransfers();
            }
        },

        /**
         * Go to previous page of transfers
         */
        prevTransfersPage() {
            if (this.transfersPage > 1) {
                this.transfersPage--;
                this.transfersPageInput = this.transfersPage;
                this.loadTransfers();
            }
        },

        /**
         * Go to specific page of transfers
         */
        goToTransfersPage() {
            const page = parseInt(this.transfersPageInput);
            if (page >= 1 && page <= this.transfersTotalPages) {
                this.transfersPage = page;
                this.loadTransfers();
            } else {
                this.transfersPageInput = this.transfersPage;
            }
        },

        /**
         * Change transfers page size
         */
        changeTransfersPageSize() {
            this.transfersPage = 1;
            this.transfersPageInput = 1;
            this.loadTransfers();
        },

        /**
         * Format timestamp to readable date
         */
        formatDate(timestamp) {
            const date = new Date(timestamp * 1000);
            return date.toLocaleString();
        },

        /**
         * Format address for display (shorten if needed)
         */
        formatAddress(address) {
            if (!address) return '';
            if (address.length > 20) {
                return `${address.substring(0, 10)}...${address.substring(address.length - 8)}`;
            }
            return address;
        },

        /**
         * Copy address to clipboard
         */
        async copyAddress(address) {
            try {
                await navigator.clipboard.writeText(address);
                // Show a brief success indicator (you could add a toast notification here)
                console.log('Address copied:', address);
                
                // Optional: Show temporary success message
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
