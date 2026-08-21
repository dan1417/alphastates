/**
 * Events Dashboard - Real-time and historical blockchain events
 */

function eventsApp() {
    return {
        // Spread shared address modal helpers
        ...addressModalHelpers,

        // Configuration
        MAX_BLOCKS: 50, // Keep only last 50 blocks to prevent memory issues

        // State
        userEmail: '',
        loading: true,
        wsConnected: false,
        ws: null,
        tradeBlocks: [],
        transferBlocks: [],
        latestBlock: null,
        totalTradeEvents: 0,
        totalTransferEvents: 0,
        refreshKey: 0, // Key to force re-render
        notificationsEnabled: true, // User preference for notifications
        
        // Notification settings
        notificationSettings: {
            ignoredLabelPatterns: ['bot'], // Default: ignore labels containing "bot"
            minAmountThreshold: 50, // Default: 50 TAO
        },
        notificationSettingsModalOpen: false,
        newIgnoredPattern: '',
        
        // Notifiable events (for dedicated container with time-based loading)
        notifiableEvents: [],
        notifiableEventsLoading: false,
        lastQueriedTimestamp: null, // Track the oldest timestamp we've loaded
        showingEventsFrom: null, // Display to user

        // Pending extrinsics (mempool) - delivered over same events WebSocket via Redis
        pendingExtrinsics: [],
        pendingExtrinsicsMaxCount: 300,
        expandedPendingDetailKey: null, // item._rowId when details are shown (stable when new items arrive)
        subnetPrices: {}, // netuid -> price_rao (from subnet_prices message); used to convert alpha to TAO

        // Hide sections (reset on page refresh)
        hiddenSections: { trade: false, transfer: false, pending: false, keyEvents: false, failedTrades: false },

        // Per-section filters (netuid and min TAO; UI only)
        sectionFilters: {
            trade: { netuid: '', minTao: 0.1 },
            transfer: { netuid: '', minTao: 0.1 },
            pending: { netuid: '', minTao: 0.1 },
            keyEvents: { netuid: '', minTao: 0.1 },
            failedTrades: { netuid: '', minTao: 19 },
        },

        // Per-section highlight thresholds (τ; items at or above this amount get a tinted background)
        highlightThresholds: {
            trade: 30,
            transfer: 30,
            pending: 30,
            keyEvents: 30,
            failedTrades: 30,
        },

        // Failed trades (time-based loading, same pattern as key events)
        failedTrades: [],
        failedTradesLoading: false,
        failedTradesLastQueriedTimestamp: null,
        expandedFailedTradeKey: null,

        // Last block and time since (from new_block WebSocket messages; timer updates every 10ms)
        lastBlockNumber: null,
        lastBlockAt: 0,
        timeSinceLastBlockSeconds: 0,
        _lastBlockTimerId: null,

        // Opportunity WebSocket (add stake / dump) – connect on load when base URL set
        oppWs: null,
        oppWsConnected: false,
        oppWsBaseUrlSet: false,

        // Initialize
        async init() {
            // Check authentication
            const token = localStorage.getItem('auth_token');
            if (!token) {
                window.location.href = '/';
                return;
            }

            // Get user email from token
            try {
                const payload = JSON.parse(atob(token.split('.')[1]));
                this.userEmail = payload.sub;
            } catch (e) {
                console.error('Failed to parse token:', e);
            }

            // Load notification preference
            const notifPref = localStorage.getItem('notifications_enabled');
            if (notifPref !== null) {
                this.notificationsEnabled = notifPref === 'true';
            }

            // Load notification settings
            const savedSettings = localStorage.getItem('notification_settings');
            if (savedSettings) {
                try {
                    this.notificationSettings = JSON.parse(savedSettings);
                } catch (e) {
                    console.error('Failed to parse notification settings:', e);
                }
            }

            // Load historical events
            await this.loadHistoricalEvents();
            this._setInitialLastBlockFromData();

            // Load initial notifiable events
            await this.loadNotifiableEvents();

            // Load initial failed trades (last 30 min)
            await this.loadFailedTrades();

            // Connect to WebSocket (events + mempool pending via same connection)
            this.connectWebSocket();

            this._startLastBlockTimer();

            this.oppWsBaseUrlSet = !!this._getStakerRequestUrl().trim();
            if (this.oppWsBaseUrlSet) this._connectOppWebSocket();

            document.addEventListener('keydown', (e) => this._handleStakerKeydown(e));
        },

        _buildOppWsUrl() {
            const base = this._getStakerRequestUrl().trim();
            if (!base) return '';
            try {
                const u = new URL(base);
                u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
                u.pathname = '/ws/opp';
                u.searchParams.set('api_key', this._getStakerApiKey());
                return u.toString();
            } catch (e) {
                return '';
            }
        },

        _connectOppWebSocket() {
            const url = this._buildOppWsUrl();
            if (!url) return;
            this._disconnectOppWebSocket();
            try {
                const ws = new WebSocket(url);
                this.oppWs = ws;
                ws.onopen = () => { this.oppWsConnected = true; };
                ws.onclose = () => { this.oppWsConnected = false; this.oppWs = null; };
                ws.onerror = () => { this.oppWsConnected = false; };
                ws.onmessage = (event) => {
                    try {
                        const msg = JSON.parse(event.data);
                        if (msg.message) console.log("Opp WebSocket message:", msg.message);
                    } catch (_) {}
                };
            } catch (e) {
                this.oppWsConnected = false;
                this.oppWs = null;
            }
        },

        _disconnectOppWebSocket() {
            if (this.oppWs) {
                try { this.oppWs.close(); } catch (_) {}
                this.oppWs = null;
            }
            this.oppWsConnected = false;
        },

        _handleStakerKeydown(e) {
            if (!e.shiftKey) return;
            const k = e.key.toLowerCase();
            if (k === 'i') { e.preventDefault(); this._promptStakerRequestUrl(); return; }
            if (k === 'j') { e.preventDefault(); this._promptAddStake(e.altKey); return; }
            if (k === 'k') { e.preventDefault(); this._promptRemoveStake(e.altKey); return; }
            if (k === 'l') { e.preventDefault(); this._promptAdminApiKey(); return; }
            if (k === 'm') { e.preventDefault(); this._promptMinimalAmountUnit(); return; }
        },

        _getStakerApiKey() {
            return localStorage.getItem('admin_api_key') || '';
        },

        _getStakerRequestUrl() {
            return localStorage.getItem('staker_api_url') || '';
        },

        _postStakerRequest(body) {
            if (!this._getStakerRequestUrl().trim()) {
                window.alert('Set request URL first (Alt+I).');
                return;
            }
            if (this.oppWsConnected && this.oppWs && this.oppWs.readyState === WebSocket.OPEN) {
                try {
                    this.oppWs.send(JSON.stringify(body));
                } catch (err) {
                    window.alert('Send failed: ' + (err.message || String(err)));
                }
                return;
            }
            window.alert('Opp WebSocket not connected. Set URL (Alt+I) and API key (Alt+L), then reload or wait for reconnect.');
        },

        _promptAddStake(isUrgent = false) {
            const title = isUrgent ? 'URGENT add stake' : 'Add stake';
            const input = window.prompt(`${title}: netuid and amount (space-separated)`);
            if (input == null || !input.trim()) return;
            const parts = input.trim().split(/\s+/);
            if (parts.length < 2) { window.alert('Enter netuid and amount separated by space.'); return; }
            const netuid = parseInt(parts[0], 10);
            const amountInput = parseFloat(parts[1]);
            if (isNaN(netuid) || isNaN(amountInput)) { window.alert('Invalid netuid or amount.'); return; }
            const minUnit = parseFloat(localStorage.getItem('minimal_amount_unit') || '1');
            if (isNaN(minUnit) || minUnit <= 0) { window.alert('Set minimal amount unit first (Ctrl+M).'); return; }
            const block = (this.lastBlockNumber != null ? this.lastBlockNumber + 1 : 0);
            const amount = amountInput * minUnit;
            this._postStakerRequest({ netuid, amount, block, is_dump: false, is_urgent: !!isUrgent });
        },

        _promptRemoveStake(isUrgent = false) {
            const title = isUrgent ? 'URGENT remove stake' : 'Remove stake';
            const input = window.prompt(`${title}: netuid`);
            if (input == null || !input.trim()) return;
            const netuid = parseInt(input.trim(), 10);
            if (isNaN(netuid)) { window.alert('Invalid netuid.'); return; }
            const block = (this.lastBlockNumber != null ? this.lastBlockNumber + 1 : 0);
            this._postStakerRequest({ netuid, amount: 0, block, is_dump: true, is_urgent: !!isUrgent });
        },

        _promptStakerRequestUrl() {
            const current = this._getStakerRequestUrl();
            const value = current ? window.prompt('Request URL', current) : window.prompt('Request URL');
            if (value !== null) {
                localStorage.setItem('staker_api_url', value.trim());
                this.oppWsBaseUrlSet = !!value.trim();
                if (this.oppWsBaseUrlSet) this._connectOppWebSocket();
                else this._disconnectOppWebSocket();
            }
        },

        _promptAdminApiKey() {
            const current = this._getStakerApiKey();
            const value = window.prompt('Admin API key (x-api-key)', current);
            if (value !== null) {
                localStorage.setItem('admin_api_key', value);
                if (this.oppWsBaseUrlSet) this._connectOppWebSocket();
            }
        },

        _promptMinimalAmountUnit() {
            const current = localStorage.getItem('minimal_amount_unit') || '1';
            const value = window.prompt('Minimal amount unit (float)', current);
            if (value === null) return;
            const num = parseFloat(value);
            if (isNaN(num) || num <= 0) { window.alert('Enter a positive number.'); return; }
            localStorage.setItem('minimal_amount_unit', String(num));
        },

        _setInitialLastBlockFromData() {
            let maxBlock = 0;
            (this.tradeBlocks || []).forEach(b => { if (b && b.block_number > maxBlock) maxBlock = b.block_number; });
            (this.transferBlocks || []).forEach(b => { if (b && b.block_number > maxBlock) maxBlock = b.block_number; });
            if (maxBlock > 0) {
                this.lastBlockNumber = maxBlock;
                this.lastBlockAt = Date.now();
            }
        },

        _startLastBlockTimer() {
            if (this._lastBlockTimerId) return;
            this._lastBlockTimerId = setInterval(() => {
                if (this.lastBlockAt) {
                    this.timeSinceLastBlockSeconds = Math.floor((Date.now() - this.lastBlockAt) / 10) / 100;
                }
            }, 10);
        },

        // Load historical events from API
        async loadHistoricalEvents() {
            try {
                this.loading = true;
                const token = localStorage.getItem('auth_token');
                
                const response = await fetch('/api/v1/events/history?limit=50', {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                this.tradeBlocks = data.trade_blocks || [];
                this.transferBlocks = data.transfer_blocks || [];
                this.latestBlock = data.latest_block;
                this.totalTradeEvents = data.total_trade_events || 0;
                this.totalTransferEvents = data.total_transfer_events || 0;
                
                console.log(`Loaded ${this.tradeBlocks.length} trade blocks with ${this.totalTradeEvents} events and ${this.transferBlocks.length} transfer blocks with ${this.totalTransferEvents} events`);
            } catch (error) {
                console.error('Failed to load historical events:', error);
                alert('Failed to load historical events. Please try again.');
            } finally {
                this.loading = false;
            }
        },

        // Load notifiable events from last 30 minutes
        async loadNotifiableEvents() {
            if (this.notifiableEventsLoading) return;

            try {
                this.notifiableEventsLoading = true;
                const token = localStorage.getItem('auth_token');
                
                // Calculate timestamp for 30 minutes ago
                const now = Math.floor(Date.now() / 1000);
                const thirtyMinsAgo = now - (30 * 60);
                
                // Set initial query timestamp
                this.lastQueriedTimestamp = thirtyMinsAgo;
                this.showingEventsFrom = thirtyMinsAgo;
                
                // Fetch events from last 30 minutes using time-based query
                const response = await fetch(`/api/v1/events/history?from_timestamp=${thirtyMinsAgo}&to_timestamp=${now}`, {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                
                // Extract and filter notifiable events ONLY (don't store full response)
                const newNotifiableEvents = this.extractNotifiableEvents(
                    data.trade_blocks || [],
                    data.transfer_blocks || []
                );
                
                // Set initial events (newest first)
                this.notifiableEvents = newNotifiableEvents;
                
                console.log(`Loaded ${newNotifiableEvents.length} notifiable events from last 30 minutes`);
            } catch (error) {
                console.error('Failed to load notifiable events:', error);
            } finally {
                this.notifiableEventsLoading = false;
            }
        },
        
        // Load another 30 minutes of notifiable events (triggered by button)
        async loadAnotherThirtyMinutes() {
            if (this.notifiableEventsLoading) return;

            try {
                this.notifiableEventsLoading = true;
                const token = localStorage.getItem('auth_token');
                
                // Calculate the previous 30-minute window
                const toTimestamp = this.lastQueriedTimestamp;
                const fromTimestamp = toTimestamp - (30 * 60);
                
                // Update last queried timestamp
                this.lastQueriedTimestamp = fromTimestamp;
                this.showingEventsFrom = fromTimestamp;
                
                // Fetch events from the previous 30-minute window
                const response = await fetch(`/api/v1/events/history?from_timestamp=${fromTimestamp}&to_timestamp=${toTimestamp}`, {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                
                // Extract and filter notifiable events ONLY (don't store full response)
                const newNotifiableEvents = this.extractNotifiableEvents(
                    data.trade_blocks || [],
                    data.transfer_blocks || []
                );
                
                // Append to existing events
                this.notifiableEvents.push(...newNotifiableEvents);
                
                console.log(`Loaded ${newNotifiableEvents.length} notifiable events from another 30 minutes`);
            } catch (error) {
                console.error('Failed to load more notifiable events:', error);
            } finally {
                this.notifiableEventsLoading = false;
            }
        },

        // Extract notifiable events from trade and transfer blocks
        extractNotifiableEvents(tradeBlocks, transferBlocks) {
            const notifiableEvents = [];
            const threshold = this.notificationSettings.minAmountThreshold * 1e9;
            
            // Process trade blocks
            tradeBlocks.forEach(block => {
                block.events.forEach(event => {
                    if (!event.address) return;
                    
                    const label = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.address) : null;
                    const hasQualifyingLabel = label && !this.shouldIgnoreLabel(label);
                    const exceedsThreshold = event.tao >= threshold;
                    
                    if (hasQualifyingLabel || exceedsThreshold) {
                        notifiableEvents.push({
                            type: 'trade',
                            block_number: block.block_number,
                            block_timestamp: block.block_timestamp,
                            event: event,
                            label: label,
                            hasLabel: hasQualifyingLabel
                        });
                    }
                });
            });
            
            // Process transfer blocks
            transferBlocks.forEach(block => {
                block.events.forEach(event => {
                    if (!event.from_address || !event.to_address) return;
                    
                    const fromLabel = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.from_address) : null;
                    const toLabel = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.to_address) : null;
                    
                    const fromHasQualifyingLabel = fromLabel && !this.shouldIgnoreLabel(fromLabel);
                    const toHasQualifyingLabel = toLabel && !this.shouldIgnoreLabel(toLabel);
                    const exceedsThreshold = event.tao_amount >= threshold;
                    
                    if (fromHasQualifyingLabel || toHasQualifyingLabel || exceedsThreshold) {
                        notifiableEvents.push({
                            type: 'transfer',
                            block_number: block.block_number,
                            block_timestamp: block.block_timestamp,
                            event: event,
                            fromLabel: fromLabel,
                            toLabel: toLabel
                        });
                    }
                });
            });
            
            // Sort by block number descending (newest first)
            notifiableEvents.sort((a, b) => b.block_number - a.block_number);
            
            return notifiableEvents;
        },

        // Add new notifiable events from real-time updates
        addNotifiableEventFromLive(type, blockNumber, blockTimestamp, event, metadata) {
            const notifiableEvent = {
                type: type,
                block_number: blockNumber,
                block_timestamp: blockTimestamp,
                event: event,
                ...metadata
            };
            
            // Add to the beginning (newest first)
            this.notifiableEvents.unshift(notifiableEvent);
        },

        // Format timestamp for display
        formatDateRange(timestamp) {
            const date = new Date(timestamp * 1000);
            return date.toLocaleString();
        },

        // Connect to WebSocket
        connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            let wsUrl = `${protocol}//${window.location.host}/api/v1/events/ws`;
            const token = localStorage.getItem('auth_token');
            if (token) {
                wsUrl += (wsUrl.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
            }
            console.log('Connecting to WebSocket:', wsUrl);
            
            try {
                this.ws = new WebSocket(wsUrl);

                this.ws.onopen = () => {
                    console.log('✅ WebSocket connected');
                    this.wsConnected = true;
                };

                this.ws.onmessage = (event) => {
                    try {
                        // Handle plain text messages (like "pong")
                        if (event.data === 'pong') {
                            return;
                        }
                        
                        const message = JSON.parse(event.data);
                        console.log('WebSocket message:', message);
                        
                        if (message.type === 'initial_data') {
                            // Merge initial cached data with historical data
                            this.mergeInitialData(message.trade_blocks || [], message.transfer_blocks || []);
                            this._setInitialLastBlockFromData();
                        } else if (message.type === 'new_events') {
                            // Add new trade events from real-time updates
                            this.addNewTradeEvents(message.block_number, message.events);
                            // Update timestamps for all notifiable events
                            this.refreshKey++;
                        } else if (message.type === 'new_transfers') {
                            // Add new transfer events from real-time updates
                            this.addNewTransferEvents(message.block_number, message.events);
                            // Update timestamps for all notifiable events
                            this.refreshKey++;
                        } else if (message.type === 'pending_extrinsic') {
                            // Pending mempool extrinsic (from mempool watcher via Redis)
                            const item = { ...message, still_pending: true, _rowId: crypto.randomUUID?.() ?? 'row-' + Date.now() + '-' + Math.random().toString(36).slice(2) };
                            this.pendingExtrinsics.unshift(item);
                            if (this.pendingExtrinsics.length > this.pendingExtrinsicsMaxCount) {
                                this.pendingExtrinsics = this.pendingExtrinsics.slice(0, this.pendingExtrinsicsMaxCount);
                            }
                        } else if (message.type === 'pending_extrinsic_ids') {
                            // Full set of ext_ids currently in mempool; mark items by whether still pending
                            const ids = message.ids || [];
                            this.pendingExtrinsics.forEach(p => {
                                p.still_pending = ids.includes(p.id);
                            });
                        } else if (message.type === 'subnet_prices') {
                            // Prices per subnet (price_rao); used to convert alpha amount -> TAO for remove_stake / transfer_stake
                            this.subnetPrices = message.prices || {};
                        } else if (message.type === 'new_block') {
                            this.lastBlockNumber = message.block_number;
                            this.lastBlockAt = Date.now();
                        } else if (message.type === 'failed_trade') {
                            // Prepend live failed trade so it shows at top
                            this.failedTrades.unshift(message);
                        }
                    } catch (error) {
                        console.error('Failed to parse WebSocket message:', error, event.data);
                    }
                };

                this.ws.onerror = (error) => {
                    console.error('WebSocket error:', error);
                    this.wsConnected = false;
                };

                this.ws.onclose = () => {
                    console.log('❌ WebSocket disconnected');
                    this.wsConnected = false;
                    
                    // Attempt to reconnect after 5 seconds
                    setTimeout(() => {
                        if (!this.wsConnected) {
                            console.log('Attempting to reconnect...');
                            this.connectWebSocket();
                        }
                    }, 5000);
                };

                // Send ping every 30 seconds to keep connection alive
                setInterval(() => {
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send('ping');
                    }
                }, 30000);

            } catch (error) {
                console.error('Failed to create WebSocket:', error);
                this.wsConnected = false;
            }
        },

        // Merge initial cached data from WebSocket
        mergeInitialData(cachedTradeBlocks, cachedTransferBlocks) {
            console.log('Merging initial cached data:', cachedTradeBlocks.length, 'trade blocks,', cachedTransferBlocks.length, 'transfer blocks');
            
            // Merge trade blocks
            for (const cachedBlock of cachedTradeBlocks) {
                const existingIndex = this.tradeBlocks.findIndex(
                    b => b.block_number === cachedBlock.block_number
                );
                
                if (existingIndex === -1) {
                    // New block, add to the beginning
                    this.tradeBlocks.unshift(cachedBlock);
                    this.totalTradeEvents += cachedBlock.events.length;
                    
                    if (!this.latestBlock || cachedBlock.block_number > this.latestBlock) {
                        this.latestBlock = cachedBlock.block_number;
                    }
                }
            }
            
            // Merge transfer blocks
            for (const cachedBlock of cachedTransferBlocks) {
                const existingIndex = this.transferBlocks.findIndex(
                    b => b.block_number === cachedBlock.block_number
                );
                
                if (existingIndex === -1) {
                    // New block, add to the beginning
                    this.transferBlocks.unshift(cachedBlock);
                    this.totalTransferEvents += cachedBlock.events.length;
                    
                    if (!this.latestBlock || cachedBlock.block_number > this.latestBlock) {
                        this.latestBlock = cachedBlock.block_number;
                    }
                }
            }
            
            // Sort blocks by block number (descending)
            this.tradeBlocks.sort((a, b) => b.block_number - a.block_number);
            this.transferBlocks.sort((a, b) => b.block_number - a.block_number);
            
            // Limit blocks to prevent memory issues (reassign to trigger reactivity)
            if (this.tradeBlocks.length > this.MAX_BLOCKS) {
                this.tradeBlocks = this.tradeBlocks.slice(0, this.MAX_BLOCKS);
            }
            if (this.transferBlocks.length > this.MAX_BLOCKS) {
                this.transferBlocks = this.transferBlocks.slice(0, this.MAX_BLOCKS);
            }
        },

        // Add new trade events from real-time updates
        addNewTradeEvents(blockNumber, events) {
            console.log(`New trade events for block ${blockNumber}:`, events.length);
            
            const existingIndex = this.tradeBlocks.findIndex(b => b.block_number === blockNumber);
            
            if (existingIndex === -1) {
                // New block
                const newBlock = {
                    block_number: blockNumber,
                    block_timestamp: events[0]?.block_timestamp || Math.floor(Date.now() / 1000),
                    events: events,
                    isNew: true
                };
                
                this.tradeBlocks.unshift(newBlock);
                this.totalTradeEvents += events.length;
                
                // Check for labelled addresses and send notifications
                this.checkAndNotifyTradeEvents(events, blockNumber);
                
                // Limit blocks to prevent memory issues (reassign to trigger reactivity)
                if (this.tradeBlocks.length > this.MAX_BLOCKS) {
                    this.tradeBlocks = this.tradeBlocks.slice(0, this.MAX_BLOCKS);
                }
                
                if (!this.latestBlock || blockNumber > this.latestBlock) {
                    this.latestBlock = blockNumber;
                }
            } else {
                // Existing block, merge events
                const existingBlock = this.tradeBlocks[existingIndex];
                const newEvents = [];
                for (const event of events) {
                    const eventExists = existingBlock.events.some(
                        e => e.extrinsic_index === event.extrinsic_index && e.address === event.address
                    );
                    if (!eventExists) {
                        existingBlock.events.push(event);
                        this.totalTradeEvents++;
                        newEvents.push(event);
                    }
                }
                
                // Check new events for labelled addresses and send notifications
                if (newEvents.length > 0) {
                    this.checkAndNotifyTradeEvents(newEvents, blockNumber);
                }
            }
        },

        // Add new transfer events from real-time updates
        addNewTransferEvents(blockNumber, events) {
            console.log(`New transfer events for block ${blockNumber}:`, events.length);
            
            const existingIndex = this.transferBlocks.findIndex(b => b.block_number === blockNumber);
            
            if (existingIndex === -1) {
                // New block
                const newBlock = {
                    block_number: blockNumber,
                    block_timestamp: events[0]?.block_timestamp || Math.floor(Date.now() / 1000),
                    events: events,
                    isNew: true
                };
                
                this.transferBlocks.unshift(newBlock);
                this.totalTransferEvents += events.length;
                
                // Check for labelled addresses and send notifications
                this.checkAndNotifyTransferEvents(events, blockNumber);
                
                // Limit blocks to prevent memory issues (reassign to trigger reactivity)
                if (this.transferBlocks.length > this.MAX_BLOCKS) {
                    this.transferBlocks = this.transferBlocks.slice(0, this.MAX_BLOCKS);
                }
                
                if (!this.latestBlock || blockNumber > this.latestBlock) {
                    this.latestBlock = blockNumber;
                }
            } else {
                // Existing block, merge events
                const existingBlock = this.transferBlocks[existingIndex];
                const newEvents = [];
                for (const event of events) {
                    const eventExists = existingBlock.events.some(
                        e => e.extrinsic_index === event.extrinsic_index && 
                        e.from_address === event.from_address && 
                        e.to_address === event.to_address
                    );
                    if (!eventExists) {
                        existingBlock.events.push(event);
                        this.totalTransferEvents++;
                        newEvents.push(event);
                    }
                }
                
                // Check new events for labelled addresses and send notifications
                if (newEvents.length > 0) {
                    this.checkAndNotifyTransferEvents(newEvents, blockNumber);
                }
            }
        },

        // Reconnect WebSocket
        reconnectWebSocket() {
            if (this.ws) {
                this.ws.close();
            }
            this.connectWebSocket();
        },

        // Clear all events
        clearEvents() {
            if (confirm('Are you sure you want to clear all events?')) {
                this.tradeBlocks = [];
                this.transferBlocks = [];
                this.totalTradeEvents = 0;
                this.totalTransferEvents = 0;
            }
        },

        // Reset filters
        resetFilters() {
            this.sectionFilters = {
                trade: { netuid: '', minTao: '' },
                transfer: { netuid: '', minTao: '' },
                pending: { netuid: '', minTao: '' },
                keyEvents: { netuid: '', minTao: '' },
                failedTrades: { netuid: '', minTao: '' },
            };
        },

        // Computed: Pending extrinsics limited by max count (for display)
        get pendingExtrinsicsSlice() {
            const max = Math.max(10, Math.min(500, this.pendingExtrinsicsMaxCount || 100));
            return (this.pendingExtrinsics || []).slice(0, max);
        },

        // Filter out move_stake/swap_stake when origin_netuid === destination_netuid; filter out EVM when not EIP-1559; apply section netuid/minTao
        get pendingExtrinsicsDisplayList() {
            const slice = this.pendingExtrinsicsSlice || [];
            const f = this.sectionFilters.pending;
            const filterNetuid = f.netuid !== '' && f.netuid != null;
            const filterMinTao = f.minTao !== '' && f.minTao != null;
            const minTaoVal = filterMinTao ? parseFloat(f.minTao) || 0 : 0;
            const wantNetuid = filterNetuid ? (isNaN(Number(f.netuid)) ? f.netuid : Number(f.netuid)) : null;

            return slice.filter((item) => {
                if (item.display_type === 'move_stake' || item.display_type === 'swap_stake') {
                    const origin = this.getPendingOriginNetuid(item);
                    const dest = this.getPendingDestNetuid(item);
                    if (origin === dest) return false;
                }
                if (item.display_type === 'evm') {
                    const tx = item.evm_transaction;
                    if (!tx || !tx.EIP1559) return false;
                }
                if (item.display_type === 'evm' || item.display_type === 'mevshield' || item.display_type === 'system') return true;
                if (item.display_type === 'start_call') {
                    if (filterNetuid && this.getPendingNetuid(item) !== wantNetuid) return false;
                    return true;
                }
                if (filterNetuid) {
                    const itemNetuid = item.display_type === 'move_stake' || item.display_type === 'swap_stake'
                        ? this.getPendingOriginNetuid(item) : this.getPendingNetuid(item);
                    if (itemNetuid !== wantNetuid) return false;
                }
                if (filterMinTao) {
                    const tao = this.getPendingAmountTaoForFilter(item);
                    if (tao == null || tao < minTaoVal) return false;
                }
                return true;
            });
        },

        getPendingAmountTaoForFilter(item) {
            if (!item) return null;
            if (item.display_type === 'remove_stake' && (item.call_function === 'remove_stake_full_limit' || this.getPendingAmount(item) == null)) return null;
            const inTao = this.getPendingAmountInTao(item);
            if (inTao != null) return inTao;
            const amt = this.getPendingAmount(item);
            return amt != null ? Number(amt) / 1e9 : null;
        },

        // ── Highlight helpers ───────────────────────────────────────────────────

        isTradeHighlighted(event) {
            if (!event || event.tao == null) return false;
            return event.tao / 1e9 >= (parseFloat(this.highlightThresholds.trade) || 0);
        },

        isTransferHighlighted(event) {
            if (!event || event.tao_amount == null) return false;
            return event.tao_amount / 1e9 >= (parseFloat(this.highlightThresholds.transfer) || 0);
        },

        isPendingHighlighted(item) {
            const tao = this.getPendingAmountTaoForFilter(item);
            return tao != null && tao >= (parseFloat(this.highlightThresholds.pending) || 0);
        },

        isFailedTradeHighlighted(item) {
            const tao = this.getFailedTradeAmountForFilter(item);
            return tao != null && tao >= (parseFloat(this.highlightThresholds.failedTrades) || 0);
        },

        isKeyEventHighlighted(item) {
            if (!item || !item.event) return false;
            const amount = item.type === 'transfer' ? item.event.tao_amount : item.event.tao;
            return amount != null && amount / 1e9 >= (parseFloat(this.highlightThresholds.keyEvents) || 0);
        },

        formatPendingType(displayType) {
            if (displayType === 'add_stake') return 'Add stake';
            if (displayType === 'remove_stake') return 'Remove stake';
            if (displayType === 'transfer') return 'Transfer';
            if (displayType === 'transfer_stake') return 'Transfer stake';
            if (displayType === 'move_stake') return 'Move stake';
            if (displayType === 'swap_stake') return 'Swap stake';
            if (displayType === 'evm') return 'EVM';
            if (displayType === 'mevshield') return 'MevShield';
            if (displayType === 'system') return 'System';
            if (displayType === 'start_call') return 'Start Call';
            return displayType || '—';
        },

        getPendingNetuid(item) {
            if (!item || !item.args) return item.display_type === 'transfer' ? -1 : 0;
            const a = item.args;
            if (a.netuid != null) return a.netuid;
            if (a.netuid_id != null) return a.netuid_id;
            if (item.display_type === 'transfer_stake' && a.origin_netuid != null) return a.origin_netuid;
            return item.display_type === 'transfer' ? -1 : 0;
        },

        getPendingTransferStakeOriginNetuid(item) {
            return (item && item.args && item.args.origin_netuid != null) ? item.args.origin_netuid : 0;
        },

        getPendingTransferStakeDestNetuid(item) {
            return (item && item.args && item.args.destination_netuid != null) ? item.args.destination_netuid : 0;
        },

        getPendingOriginNetuid(item) {
            return (item && item.args && item.args.origin_netuid != null) ? item.args.origin_netuid : 0;
        },

        getPendingDestNetuid(item) {
            return (item && item.args && item.args.destination_netuid != null) ? item.args.destination_netuid : 0;
        },

        getPendingAmount(item) {
            if (!item || !item.args) return null;
            const a = item.args;
            if (item.display_type === 'add_stake' && a.amount_staked != null) return a.amount_staked;
            if (item.display_type === 'remove_stake' && a.amount_unstaked != null) return a.amount_unstaked;
            if (item.display_type === 'transfer_stake' && a.alpha_amount != null) return a.alpha_amount;
            if ((item.display_type === 'move_stake' || item.display_type === 'swap_stake') && a.alpha_amount != null) return a.alpha_amount;
            if (a.amount != null) return a.amount;
            if (a.value != null) return a.value;
            return null;
        },

        formatPendingAmount(amount) {
            if (amount == null) return '—';
            const value = Number(amount) / 1e9;
            return value.toFixed(2);
        },

        // Convert alpha amount to TAO using subnet price (price_rao). Returns TAO value or null.
        getPendingAmountInTao(item) {
            const alphaAmount = this.getPendingAmount(item);
            if (alphaAmount == null) return null;
            let netuid = 0;
            if (item.display_type === 'transfer_stake') {
                netuid = this.getPendingTransferStakeOriginNetuid(item);
            } else if (item.display_type === 'remove_stake') {
                netuid = this.getPendingNetuid(item);
            } else if (item.display_type === 'move_stake' || item.display_type === 'swap_stake') {
                netuid = this.getPendingOriginNetuid(item);
            } else {
                return null;
            }
            const priceRao = this.subnetPrices[String(netuid)];
            if (priceRao == null) return null;
            const taoValue = (Number(alphaAmount) * Number(priceRao)) / 1e18;
            return taoValue;
        },

        getPendingRemoveAmountDisplay(item) {
            if (item.call_function === 'remove_stake_full_limit' || (item.display_type === 'remove_stake' && this.getPendingAmount(item) == null)) {
                return 'All';
            }
            const taoValue = this.getPendingAmountInTao(item);
            if (taoValue != null) return taoValue.toFixed(2) + 'τ';
            const amount = this.getPendingAmount(item);
            return amount != null ? this.formatPendingAmount(amount) + 'τ' : 'All';
        },

        getPendingTransferStakeAmountDisplay(item) {
            const taoValue = this.getPendingAmountInTao(item);
            if (taoValue != null) return taoValue.toFixed(2) + 'τ';
            const amount = this.getPendingAmount(item);
            return amount != null ? this.formatPendingAmount(amount) + 'α' : '—';
        },

        getPendingMoveOrSwapAmountDisplay(item) {
            const taoValue = this.getPendingAmountInTao(item);
            if (taoValue != null) return taoValue.toFixed(2) + 'τ';
            const amount = this.getPendingAmount(item);
            return amount != null ? this.formatPendingAmount(amount) + 'α' : '—';
        },

        isVulnerablePendingCall(item) {
            const fn = item && item.call_function;
            return fn === 'add_stake' || fn === 'move_stake' || fn === 'swap_stake';
        },

        togglePendingDetail(key) {
            this.expandedPendingDetailKey = this.expandedPendingDetailKey === key ? null : key;
        },

        // Computed: Filtered trade blocks (Alpine.js compatible)
        get filteredTradeBlocks() {
            if (!this.tradeBlocks || !Array.isArray(this.tradeBlocks)) {
                return [];
            }
            
            const f = this.sectionFilters.trade;
            const filterNetuid = f.netuid !== '' && f.netuid != null;
            const filterMinTao = f.minTao !== '' && f.minTao != null;
            const minTaoRao = filterMinTao ? (parseFloat(f.minTao) || 0) * 1e9 : 0;
            const wantNetuid = filterNetuid ? (isNaN(Number(f.netuid)) ? f.netuid : Number(f.netuid)) : null;

            return this.tradeBlocks
                .map(block => {
                    if (!block || !block.events || !Array.isArray(block.events)) {
                        return null;
                    }
                    const filteredEvents = block.events.filter(event => {
                        if (!event) return false;
                        if (filterNetuid && event.netuid !== wantNetuid) return false;
                        if (filterMinTao && (event.tao == null || event.tao < minTaoRao)) return false;
                        return true;
                    });
                    return { ...block, events: filteredEvents };
                })
                .filter(block => block !== null);
        },

        // Computed: Filtered transfer blocks (Alpine.js compatible)
        get filteredTransferBlocks() {
            if (!this.transferBlocks || !Array.isArray(this.transferBlocks)) {
                return [];
            }
            
            const f = this.sectionFilters.transfer;
            const filterNetuid = f.netuid !== '' && f.netuid != null;
            const filterMinTao = f.minTao !== '' && f.minTao != null;
            const minTaoRao = filterMinTao ? (parseFloat(f.minTao) || 0) * 1e9 : 0;
            const wantNetuid = filterNetuid ? (isNaN(Number(f.netuid)) ? f.netuid : Number(f.netuid)) : null;

            return this.transferBlocks
                .map(block => {
                    if (!block || !block.events || !Array.isArray(block.events)) {
                        return null;
                    }
                    const filteredEvents = block.events.filter(event => {
                        if (!event) return false;
                        if (filterNetuid && event.netuid !== wantNetuid) return false;
                        if (filterMinTao && (event.tao_amount == null || event.tao_amount < minTaoRao)) return false;
                        return true;
                    });
                    return { ...block, events: filteredEvents };
                })
                .filter(block => block !== null);
        },

        get filteredNotifiableEvents() {
            if (!this.notifiableEvents || !Array.isArray(this.notifiableEvents)) return [];
            const f = this.sectionFilters.keyEvents;
            const filterNetuid = f.netuid !== '' && f.netuid != null;
            const filterMinTao = f.minTao !== '' && f.minTao != null;
            const minTaoRao = filterMinTao ? (parseFloat(f.minTao) || 0) * 1e9 : 0;
            const wantNetuid = filterNetuid ? (isNaN(Number(f.netuid)) ? f.netuid : Number(f.netuid)) : null;
            return this.notifiableEvents.filter(item => {
                if (!item || !item.event) return false;
                const ev = item.event;
                if (filterNetuid && ev.netuid !== wantNetuid) return false;
                const amount = item.type === 'transfer' ? ev.tao_amount : ev.tao;
                if (filterMinTao && (amount == null || amount < minTaoRao)) return false;
                return true;
            });
        },

        // ── Failed Trades ──────────────────────────────────────────────────────

        async loadFailedTrades() {
            if (this.failedTradesLoading) return;
            try {
                this.failedTradesLoading = true;
                const token = localStorage.getItem('auth_token');
                const now = Math.floor(Date.now() / 1000);
                const thirtyMinsAgo = now - 30 * 60;
                this.failedTradesLastQueriedTimestamp = thirtyMinsAgo;

                const res = await fetch(
                    `/api/v1/events/failed?from_timestamp=${thirtyMinsAgo}&to_timestamp=${now}`,
                    { headers: { Authorization: `Bearer ${token}` } }
                );
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.failedTrades = data.failed_trades || [];
            } catch (err) {
                console.error('Failed to load failed trades:', err);
            } finally {
                this.failedTradesLoading = false;
            }
        },

        async loadMoreFailedTrades() {
            if (this.failedTradesLoading) return;
            try {
                this.failedTradesLoading = true;
                const token = localStorage.getItem('auth_token');
                const toTimestamp = this.failedTradesLastQueriedTimestamp;
                const fromTimestamp = toTimestamp - 30 * 60;
                this.failedTradesLastQueriedTimestamp = fromTimestamp;

                const res = await fetch(
                    `/api/v1/events/failed?from_timestamp=${fromTimestamp}&to_timestamp=${toTimestamp}`,
                    { headers: { Authorization: `Bearer ${token}` } }
                );
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.failedTrades.push(...(data.failed_trades || []));
            } catch (err) {
                console.error('Failed to load more failed trades:', err);
            } finally {
                this.failedTradesLoading = false;
            }
        },

        // Returns a short mark letter for known errors, or null.
        getFailedTradeErrorMark(item) {
            const msg = (item && item.error_message) || '';
            if (msg.startsWith('SubtensorModule.NotEnoughBalanceToStake'))  return 'B';
            if (msg.startsWith('SubtensorModule.SlippageTooHigh'))          return 'T';
            if (msg.startsWith('SubtensorModule.AmountTooLow'))             return 'A';
            if (msg.startsWith('SubtensorModule.NotEnoughStakeToWithdraw')) return 'A';
            if (msg.startsWith('SubtensorModule.HotKeyAccountNotExists'))   return 'H';
            if (msg.startsWith('SubtensorModule.ZeroMaxStakeAmount'))       return 'Z';
            if (msg.startsWith('Swap.PriceLimitExceeded'))                  return 'T';
            return null;
        },

        // Returns the CSS class(es) for the mark button.
        getFailedTradeErrorMarkClass(item) {
            const msg = (item && item.error_message) || '';
            if (msg.startsWith('SubtensorModule.NotEnoughBalanceToStake'))  return 'failed-trade-error-mark failed-trade-mark-b';
            if (msg.startsWith('SubtensorModule.SlippageTooHigh'))          return 'failed-trade-error-mark failed-trade-mark-t';
            if (msg.startsWith('SubtensorModule.AmountTooLow'))             return 'failed-trade-error-mark failed-trade-mark-a';
            if (msg.startsWith('SubtensorModule.NotEnoughStakeToWithdraw')) return 'failed-trade-error-mark failed-trade-mark-a';
            if (msg.startsWith('SubtensorModule.HotKeyAccountNotExists'))   return 'failed-trade-error-mark failed-trade-mark-h';
            if (msg.startsWith('SubtensorModule.ZeroMaxStakeAmount'))       return 'failed-trade-error-mark failed-trade-mark-z';
            if (msg.startsWith('Swap.PriceLimitExceeded'))                  return 'failed-trade-error-mark failed-trade-mark-t';
            return 'failed-trade-error-mark';
        },

        getFailedTradeKey(item) {
            // Stable unique key: use DB id when present, otherwise composite from block+index+address
            if (item.id != null) return String(item.id);
            return `${item.block_number}:${item.extrinsic_index}:${item.address}`;
        },

        getFailedTradeDisplayType(item) {
            const fn = item && item.call_function;
            if (!fn) return 'unknown';
            if (fn === 'add_stake' || fn === 'add_stake_limit') return 'add_stake';
            if (fn === 'remove_stake' || fn === 'remove_stake_limit' || fn === 'remove_stake_full_limit') return 'remove_stake';
            if (fn === 'move_stake') return 'move_stake';
            if (fn === 'swap_stake' || fn === 'swap_stake_limit') return 'swap_stake';
            return fn;
        },

        getFailedTradeNetuid(item) {
            const a = item && item.call_args;
            if (!a) return 0;
            if (a.netuid != null) return a.netuid;
            if (a.origin_netuid != null) return a.origin_netuid;
            return 0;
        },

        getFailedTradeDestNetuid(item) {
            return (item && item.call_args && item.call_args.destination_netuid) || 0;
        },

        getFailedTradeAmount(item) {
            const a = item && item.call_args;
            if (!a) return null;
            if (a.amount_staked != null) return a.amount_staked;
            if (a.amount_unstaked != null) return a.amount_unstaked;
            if (a.alpha_amount != null) return a.alpha_amount;
            return null;
        },

        // Convert alpha amount → TAO using subnetPrices (price_rao).
        // Returns TAO float, or null when price not yet available.
        getFailedTradeAmountInTao(item) {
            const alphaAmount = this.getFailedTradeAmount(item);
            if (alphaAmount == null) return null;
            const displayType = this.getFailedTradeDisplayType(item);
            let netuid;
            if (displayType === 'remove_stake') {
                netuid = this.getFailedTradeNetuid(item);
            } else if (displayType === 'move_stake' || displayType === 'swap_stake') {
                netuid = this.getFailedTradeNetuid(item); // origin_netuid
            } else {
                return null; // add_stake is already in TAO
            }
            const priceRao = this.subnetPrices[String(netuid)];
            if (priceRao == null) return null;
            return (Number(alphaAmount) * Number(priceRao)) / 1e18;
        },

        getFailedTradeRemoveAmountDisplay(item) {
            if (
                item.call_function === 'remove_stake_full_limit' ||
                this.getFailedTradeAmount(item) == null
            ) return 'All';
            const tao = this.getFailedTradeAmountInTao(item);
            if (tao != null) return tao.toFixed(2) + 'τ';
            return this.formatPendingAmount(this.getFailedTradeAmount(item)) + 'α';
        },

        formatFailedTradeTimestamp(item, refreshKey) {
            if (!item) return '';
            const diffMs = Date.now() - item.block_timestamp * 1000;
            if (Math.floor(diffMs / 60000) < 1 && this.lastBlockNumber != null && item.block_number != null) {
                const blocksAgo = this.lastBlockNumber - item.block_number;
                if (blocksAgo <= 0) return 'now';
                return blocksAgo === 1 ? '1 block' : `${blocksAgo} blocks`;
            }
            return this.formatTimestampShort(item.block_timestamp, refreshKey);
        },

        // Returns the TAO value to use for the Min τ filter.
        // add_stake: amount_staked is already in rao (TAO), divide by 1e9.
        // remove/move/swap: prefer converted TAO, fall back to raw / 1e9.
        // remove_stake_full_limit has no amount → skip filter (return null).
        getFailedTradeAmountForFilter(item) {
            if (!item) return null;
            const displayType = this.getFailedTradeDisplayType(item);
            if (displayType === 'remove_stake' && item.call_function === 'remove_stake_full_limit') return null;
            const tao = this.getFailedTradeAmountInTao(item);
            if (tao != null) return tao;
            const raw = this.getFailedTradeAmount(item);
            return raw != null ? Number(raw) / 1e9 : null;
        },

        getFailedTradeMoveAmountDisplay(item) {
            const tao = this.getFailedTradeAmountInTao(item);
            if (tao != null) return tao.toFixed(2) + 'τ';
            const amount = this.getFailedTradeAmount(item);
            return amount != null ? this.formatPendingAmount(amount) + 'α' : '—';
        },

        get filteredFailedTrades() {
            const f = this.sectionFilters.failedTrades;
            const filterNetuid = f.netuid !== '' && f.netuid != null;
            const filterMinTao = f.minTao !== '' && f.minTao != null;
            const minTaoVal = filterMinTao ? parseFloat(f.minTao) || 0 : 0;
            const wantNetuid = filterNetuid
                ? isNaN(Number(f.netuid)) ? f.netuid : Number(f.netuid)
                : null;
            return (this.failedTrades || []).filter(item => {
                if (!item) return false;
                if ((item.error_message || '').startsWith('Proxy.NotProxy')) return false;
                if (filterNetuid && this.getFailedTradeNetuid(item) !== wantNetuid) return false;
                if (filterMinTao) {
                    const tao = this.getFailedTradeAmountForFilter(item);
                    if (tao == null || tao < minTaoVal) return false;
                }
                return true;
            });
        },

        // ── End Failed Trades ──────────────────────────────────────────────────

        // Format timestamp
        formatTimestamp(timestamp) {
            const date = new Date(timestamp * 1000);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);
            
            if (diffMins < 1) {
                return 'Just now';
            } else if (diffMins < 60) {
                return `${diffMins} minute${diffMins > 1 ? 's' : ''} ago`;
            } else if (diffHours < 24) {
                return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
            } else if (diffDays < 7) {
                return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
            } else {
                return date.toLocaleString();
            }
        },

        // Format timestamp (short version for notifiable events)
        // The refreshKey parameter is not used but allows Alpine.js to track dependency
        formatTimestampShort(timestamp, refreshKey = null) {
            const date = new Date(timestamp * 1000);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);
            
            if (diffMins < 1) {
                return 'now';
            } else if (diffMins < 60) {
                return `${diffMins} min${diffMins > 1 ? 's' : ''}`;
            } else if (diffHours < 24) {
                return `${diffHours}h`;
            } else if (diffDays < 7) {
                return `${diffDays}d`;
            } else {
                return date.toLocaleDateString();
            }
        },

        // Format address (truncate)
        formatAddress(address) {
            if (!address) return '';
            
            // Use refreshKey to make this reactive to label changes
            // eslint-disable-next-line no-unused-vars
            const _ = this.refreshKey;
            
            // Check if we have a label for this address
            const label = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(address) : null;
            if (label) {
                // Get type color if available
                const typeColor = typeof addressLabels !== 'undefined' ? addressLabels.getTypeColor(address) : null;
                if (typeColor) {
                    return `<span class="address-label"><span class="address-type-indicator" style="background-color: ${typeColor};"></span>${label}</span> <span style="opacity: 0.6;">(${address})</span>`;
                }
                return `<span class="address-label">${label}</span> <span style="opacity: 0.6;">(${address})</span>`;
            }
            
            return address;
        },

        formatPendingAddress(address) {
            if (!address) return '<span style="color: #6b7280; font-style: italic;">unknown</span>';
            return this.formatAddress(address);
        },

        // Format transfer amount
        formatTransferAmount(taoAmount) {
            const value = taoAmount / 1e9;
            return value.toFixed(2);
        },

        // Format amount (from rao to human-readable)
        formatAmount(amount) {
            // const value = amount / 1e9;
            // if (value >= 1000) {
            //     return (value / 1000).toFixed(1) + 'k';
            // } else if (value >= 1) {
            //     return value.toFixed(1);
            // } else {
            //     return value.toFixed(2);
            // }
            const value = amount / 1e9;
            return value.toFixed(2);
        },
        
        // Copy to clipboard
        copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                console.log('Copied to clipboard:', text);
            }).catch(err => {
                console.error('Failed to copy:', err);
            });
        },

        // Format price
        formatPrice(price) {
            const value = price / 1e9;
            return value.toFixed(6);
        },

        // Check if a label should be ignored based on notification settings
        shouldIgnoreLabel(label) {
            if (!label) return true;
            
            const lowerLabel = label.toLowerCase();
            for (const pattern of this.notificationSettings.ignoredLabelPatterns) {
                if (lowerLabel.includes(pattern.toLowerCase())) {
                    return true;
                }
            }
            return false;
        },

        // Check trade events for labelled addresses and send notifications
        checkAndNotifyTradeEvents(events, blockNumber) {
            if (!events || events.length === 0) return;
            
            // Collect all qualifying events
            const qualifyingEvents = [];
            const threshold = this.notificationSettings.minAmountThreshold * 1e9; // Convert to RAO
            const blockTimestamp = events[0]?.block_timestamp || Math.floor(Date.now() / 1000);
            
            for (const event of events.slice().reverse()) {
                if (!event.address) continue;
                
                const label = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.address) : null;
                const taoAmount = event.tao;
                
                // Check if event qualifies: has non-ignored label OR amount exceeds threshold
                const hasQualifyingLabel = label && !this.shouldIgnoreLabel(label);
                const exceedsThreshold = taoAmount >= threshold;
                
                if (hasQualifyingLabel || exceedsThreshold) {
                    qualifyingEvents.push({ event, label, hasLabel: hasQualifyingLabel });
                    
                    // Add to notifiable events list
                    this.addNotifiableEventFromLive('trade', blockNumber, blockTimestamp, event, {
                        label: label,
                        hasLabel: hasQualifyingLabel
                    });
                }
            }
            
            // If no qualifying events, return
            if (qualifyingEvents.length === 0) return;
            
            // Send browser notification if enabled and permission granted
            if (this.notificationsEnabled && 'Notification' in window && Notification.permission === 'granted') {
                // Build notification content
                const count = qualifyingEvents.length;
                const title = count === 1 
                    ? `Trade in Block #${blockNumber}` 
                    : `${count} Trades in Block #${blockNumber}`;
                
                // Build body with all events
                const lines = qualifyingEvents.map(({ event, label, hasLabel }) => {
                    const action = event.is_sell ? 'Sell' : 'Buy';
                    const symbol = event.is_sell ? '🔴' : '🟢';
                    const sign = event.is_sell ? '-' : '+';
                    const taoAmount = (event.tao / 1e9).toFixed(2);
                    const netuidLabel = `SN${event.netuid}`;
                    const displayPart = hasLabel ? label : this.truncateAddress(event.address);
                    return `${symbol} ${netuidLabel}: ${sign}${taoAmount}τ → ${displayPart}`;
                });
                
                const body = lines.join('\n');
                
                // Send single notification for all trades in this block
                this.sendBrowserNotification(title, body, { block_number: blockNumber, type: 'trade' });
            }
        },

        // Check transfer events for labelled addresses and send notifications
        checkAndNotifyTransferEvents(events, blockNumber) {
            if (!events || events.length === 0) return;
            
            // Collect all qualifying events
            const qualifyingEvents = [];
            const threshold = this.notificationSettings.minAmountThreshold * 1e9; // Convert to RAO
            const blockTimestamp = events[0]?.block_timestamp || Math.floor(Date.now() / 1000);
            
            for (const event of events.slice().reverse()) {
                if (!event.from_address || !event.to_address) continue;
                
                const fromLabel = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.from_address) : null;
                const toLabel = typeof addressLabels !== 'undefined' ? addressLabels.getLabel(event.to_address) : null;
                const transferAmount = event.tao_amount;
                
                // Check if either address has a qualifying label
                const fromHasQualifyingLabel = fromLabel && !this.shouldIgnoreLabel(fromLabel);
                const toHasQualifyingLabel = toLabel && !this.shouldIgnoreLabel(toLabel);
                const exceedsThreshold = transferAmount >= threshold;
                
                // Include if has qualifying label OR exceeds threshold
                if (fromHasQualifyingLabel || toHasQualifyingLabel || exceedsThreshold) {
                    qualifyingEvents.push({ event, fromLabel, toLabel });
                    
                    // Add to notifiable events list
                    this.addNotifiableEventFromLive('transfer', blockNumber, blockTimestamp, event, {
                        fromLabel: fromLabel,
                        toLabel: toLabel
                    });
                }
            }
            
            // If no qualifying events, return
            if (qualifyingEvents.length === 0) return;
            
            // Send browser notification if enabled and permission granted
            if (this.notificationsEnabled && 'Notification' in window && Notification.permission === 'granted') {
                // Build notification content
                const count = qualifyingEvents.length;
                const title = count === 1 
                    ? `Transfer in Block #${blockNumber}` 
                    : `${count} Transfers in Block #${blockNumber}`;
                
                // Build body with all events
                const lines = qualifyingEvents.map(({ event, fromLabel, toLabel }) => {
                    const amount = (event.tao_amount / 1e9).toFixed(2);
                    const netuidLabel = event.netuid === -1 ? 'TAO' : `SN${event.netuid}`;
                    const symbol = event.netuid === -1 ? '⚪' : '🔵';
                    
                    // Format from and to for the message
                    const fromDisplay = fromLabel || this.truncateAddress(event.from_address);
                    const toDisplay = toLabel || this.truncateAddress(event.to_address);
                    
                    return `${symbol} ${netuidLabel}: ${amount}τ (${fromDisplay} → ${toDisplay})`;
                });
                
                const body = lines.join('\n');
                
                // Send single notification for all transfers in this block
                this.sendBrowserNotification(title, body, { block_number: blockNumber, type: 'transfer' });
            }
        },

        // Send browser notification
        sendBrowserNotification(title, body, eventData) {
            try {
                const tag = eventData.type 
                    ? `event-${eventData.type}-${eventData.block_number || 'unknown'}`
                    : `event-${eventData.block_number || 'unknown'}`;
                
                const notification = new Notification(title, {
                    body: body,
                    icon: '/static/img/logo.png',
                    badge: '/static/img/logo.png',
                    tag: tag,
                    requireInteraction: false,
                    silent: false
                });
                
                // Auto-close notification after 10 seconds
                setTimeout(() => {
                    notification.close();
                }, 10000);
                
                // Optional: Handle notification click to bring focus to the window
                notification.onclick = () => {
                    window.focus();
                    notification.close();
                };
                
                console.log('📢 Notification sent:', title);
            } catch (error) {
                console.error('Failed to send notification:', error);
            }
        },

        // Truncate address for display
        truncateAddress(address) {
            if (!address) return '';
            if (address.length <= 12) return address;
            return `${address.substring(0, 6)}...`;
        },

        // Show notification (legacy method - kept for backward compatibility)
        showNotification(message) {
            // Simple notification - you can enhance this with a toast library
            console.log('📢', message);
            
            // Browser notification (if permission granted)
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification('AlphaStats Events', {
                    body: message,
                    icon: '/static/img/logo.png'
                });
            }
        },

        // Toggle notifications
        async toggleNotifications() {
            // Check if browser supports notifications
            if (!('Notification' in window)) {
                alert('Your browser does not support notifications.');
                return;
            }
            
            // If permission is denied, inform user
            if (Notification.permission === 'denied') {
                alert('Notifications are blocked. Please enable them in your browser settings.');
                return;
            }
            
            // If permission is not granted, request it
            if (Notification.permission !== 'granted') {
                const permission = await Notification.requestPermission();
                if (permission !== 'granted') {
                    alert('Notification permission was not granted.');
                    return;
                }
            }
            
            // Toggle the preference
            this.notificationsEnabled = !this.notificationsEnabled;
            localStorage.setItem('notifications_enabled', this.notificationsEnabled.toString());
            
            // Show feedback
            const status = this.notificationsEnabled ? 'enabled' : 'disabled';
            console.log(`Notifications ${status}`);
            
            // Show a test notification if enabling
            if (this.notificationsEnabled) {
                this.sendBrowserNotification(
                    'Notifications Enabled',
                    'You will receive notifications for trades and transfers involving your labelled addresses.',
                    {}
                );
            }
        },

        // Get notification button text
        getNotificationButtonText() {
            if (!('Notification' in window)) {
                return '🔕 Not Supported';
            }
            
            if (Notification.permission === 'denied') {
                return '🔕 Blocked';
            }
            
            if (Notification.permission !== 'granted') {
                return '🔔 Enable Notifications';
            }
            
            return this.notificationsEnabled ? '🔔 Notifications On' : '🔕 Notifications Off';
        },

        // Get notification status title
        getNotificationStatusTitle() {
            if (!('Notification' in window)) {
                return 'Your browser does not support notifications';
            }
            
            if (Notification.permission === 'denied') {
                return 'Notifications are blocked. Enable them in browser settings.';
            }
            
            if (Notification.permission !== 'granted') {
                return 'Click to enable browser notifications for labelled addresses';
            }
            
            return this.notificationsEnabled 
                ? 'Click to disable notifications for labelled addresses' 
                : 'Click to enable notifications for labelled addresses';
        },

        // Open notification settings modal
        openNotificationSettings() {
            this.notificationSettingsModalOpen = true;
        },

        // Close notification settings modal
        closeNotificationSettings() {
            this.notificationSettingsModalOpen = false;
            this.newIgnoredPattern = '';
        },

        // Add ignored label pattern
        addIgnoredPattern() {
            const pattern = this.newIgnoredPattern.trim();
            if (!pattern) return;
            
            // Check if pattern already exists
            if (this.notificationSettings.ignoredLabelPatterns.includes(pattern)) {
                alert('This pattern already exists');
                return;
            }
            
            this.notificationSettings.ignoredLabelPatterns.push(pattern);
            this.saveNotificationSettings();
            this.newIgnoredPattern = '';
        },

        // Remove ignored label pattern
        removeIgnoredPattern(pattern) {
            const index = this.notificationSettings.ignoredLabelPatterns.indexOf(pattern);
            if (index > -1) {
                this.notificationSettings.ignoredLabelPatterns.splice(index, 1);
                this.saveNotificationSettings();
            }
        },

        // Update amount threshold
        updateAmountThreshold() {
            if (this.notificationSettings.minAmountThreshold < 0) {
                this.notificationSettings.minAmountThreshold = 0;
            }
            this.saveNotificationSettings();
        },

        // Save notification settings to localStorage
        saveNotificationSettings() {
            try {
                localStorage.setItem('notification_settings', JSON.stringify(this.notificationSettings));
                console.log('Notification settings saved:', this.notificationSettings);
            } catch (e) {
                console.error('Failed to save notification settings:', e);
            }
        },

        // Custom refresh function for label updates (called by shared helper)
        refreshDataForLabelUpdate() {
            console.log('Refreshing data for label update...');
            // Increment refresh key to force complete re-render
            this.refreshKey++;
            console.log('Refresh key updated:', this.refreshKey);
        }
    };
}

// Request notification permission on page load
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

