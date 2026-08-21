/**
 * Subnet Analytics Page
 * Handles OHLC chart rendering
 */

function subnetApp() {
    return {
        // Spread shared address modal helpers
        ...addressModalHelpers,

        // State
        netuid: null,
        subnetInput: '',
        interval: '4H',
        timezone: 'local', // Default to user's local timezone
        loading: false,
        error: null,
        candleCount: 0,
        lastUpdated: '',
        
        // Chart references
        chart: null,
        candlestickSeries: null,
        selectionOverlay: null, // HTML overlay for visual selection
        
        // OHLC data
        currentPrice: null,

        // Subnet info (from subnets-cacher snapshot, pushed via WebSocket)
        subnetInfo: null,
        subnetInfoBlock: null,
        liveConnected: false,
        subnetWs: null,
        _subnetWsReconnectTimer: null,
        _subnetWsPingTimer: null,
        
        // Data management
        loadedData: new Map(), // Store all loaded candles by timestamp
        debounceTimer: null,
        isUpdatingChart: false, // Flag to prevent fetch during chart updates
        refreshTimer: null, // Timer for periodic updates
        refreshInterval: 12000, // 12 seconds
        chartLive: false, // True while the periodic refresh timer is active
        
        // Selection feature
        selectionActive: false,
        selectionStart: null,
        selectionEnd: null,
        selectionPreview: null,
        selectionStats: null,
        
        // Trade data
        tradeData: null,
        loadingTrades: false,

        // Table sorting
        buyersSortColumn: 'net_alpha',
        buyersSortDirection: 'desc',
        sellersSortColumn: 'net_alpha',
        sellersSortDirection: 'desc',
        gainersSortColumn: 'tao_net',
        gainersSortDirection: 'desc',
        losersSortColumn: 'tao_net',
        losersSortDirection: 'asc',

        // Address trading activity
        addressFilter: '',
        addressTrades: null,
        loadingAddressTrades: false,
        tradeMarkers: [],
        markerData: new Map(), // Store marker details for tooltips
        markerTooltip: null, // Tooltip element

        /**
         * Initialize the app
         */
        async init() {
            // Extract netuid from URL
            const pathParts = window.location.pathname.split('/');
            this.netuid = parseInt(pathParts[pathParts.length - 1]);
            
            if (isNaN(this.netuid)) {
                // Invalid netuid, redirect to home
                window.location.href = '/';
                return;
            }

            // Update page title
            document.title = `SN ${this.netuid} | AlphaStats`;

            // Initialize chart
            this.initChart();

            // One-shot REST fetch for fast initial render; live updates come over WS.
            this.loadSubnetInfo();
            this.connectSubnetWebSocket();

            // Clean up WS on page unload
            window.addEventListener('beforeunload', () => this.disconnectSubnetWebSocket());

            // Load initial data with dynamic time range based on interval
            const { startTime, endTime } = this.getInitialTimeRange();
            await this.loadOHLCData(startTime, endTime, true); // true = fit content on initial load

            // Start periodic refresh for real-time updates
            this.startPeriodicRefresh();
        },

        /**
         * Load subnet info (identity, owners, AMM state) from cached snapshot.
         */
        async loadSubnetInfo() {
            try {
                const response = await authFetch(`/api/v1/subnets/${this.netuid}`);
                if (!response.ok) {
                    if (response.status === 404) {
                        console.warn(`Subnet ${this.netuid} not present in latest snapshot`);
                    } else {
                        console.warn(`Failed to load subnet info: ${response.status} ${response.statusText}`);
                    }
                    return;
                }
                const data = await response.json();
                this.subnetInfo = data.subnet;
                this.subnetInfoBlock = data.block_number;
            } catch (err) {
                console.error('Error loading subnet info:', err);
            }
        },

        /**
         * Format a token amount as x.yy, x.yyK, or x.yyM.
         */
        formatAmount(value) {
            const n = Number(value);
            if (!isFinite(n)) return '—';
            const abs = Math.abs(n);
            if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
            if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K';
            return n.toFixed(2);
        },

        /**
         * Format a unix-seconds timestamp as "YYYY-MM-DD HH:MM UTC".
         */
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

        /**
         * Whether the subnet identity contains any link/contact fields.
         */
        hasIdentityLinks() {
            const id = this.subnetInfo?.subnet_identity;
            if (!id) return false;
            return Boolean(id.subnet_url || id.github_repo || id.discord || id.subnet_contact);
        },

        /**
         * Open a WebSocket to receive a fresh subnet snapshot on every new block.
         */
        connectSubnetWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            let wsUrl = `${protocol}//${window.location.host}/api/v1/subnets/${this.netuid}/ws`;
            const token = localStorage.getItem('auth_token');
            if (token) {
                wsUrl += '?token=' + encodeURIComponent(token);
            }

            try {
                this.subnetWs = new WebSocket(wsUrl);
            } catch (err) {
                console.error('Failed to create subnet WebSocket:', err);
                this._scheduleSubnetWsReconnect();
                return;
            }

            this.subnetWs.onopen = () => {
                console.log('✅ Subnet WS connected');
                this.liveConnected = true;
                // Keepalive ping every 30s
                this._subnetWsPingTimer = setInterval(() => {
                    if (this.subnetWs && this.subnetWs.readyState === WebSocket.OPEN) {
                        this.subnetWs.send('ping');
                    }
                }, 30000);
            };

            this.subnetWs.onmessage = (event) => {
                if (event.data === 'pong') return;
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'subnet_snapshot' && msg.subnet) {
                        this.subnetInfo = msg.subnet;
                        this.subnetInfoBlock = msg.block_number;
                    }
                } catch (err) {
                    console.error('Subnet WS message parse error:', err);
                }
            };

            this.subnetWs.onerror = (err) => {
                console.error('Subnet WS error:', err);
            };

            this.subnetWs.onclose = () => {
                console.log('❌ Subnet WS disconnected');
                this.liveConnected = false;
                this.subnetWs = null;
                if (this._subnetWsPingTimer) {
                    clearInterval(this._subnetWsPingTimer);
                    this._subnetWsPingTimer = null;
                }
                this._scheduleSubnetWsReconnect();
            };
        },

        _scheduleSubnetWsReconnect() {
            if (this._subnetWsReconnectTimer) return;
            this._subnetWsReconnectTimer = setTimeout(() => {
                this._subnetWsReconnectTimer = null;
                this.connectSubnetWebSocket();
            }, 5000);
        },

        disconnectSubnetWebSocket() {
            if (this._subnetWsReconnectTimer) {
                clearTimeout(this._subnetWsReconnectTimer);
                this._subnetWsReconnectTimer = null;
            }
            if (this._subnetWsPingTimer) {
                clearInterval(this._subnetWsPingTimer);
                this._subnetWsPingTimer = null;
            }
            if (this.subnetWs) {
                try { this.subnetWs.close(); } catch (e) {}
                this.subnetWs = null;
            }
            this.liveConnected = false;
        },

        /**
         * Format timestamp according to selected timezone
         */
        formatTimestamp(timestamp, options = {}) {
            const date = new Date(timestamp * 1000);
            const tz = this.timezone === 'local' ? undefined : this.timezone;
            
            const defaultOptions = {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                timeZone: tz,
                hour12: false
            };
            
            return date.toLocaleString('en-US', { ...defaultOptions, ...options });
        },

        /**
         * Get timezone abbreviation
         */
        getTimezoneAbbr() {
            if (this.timezone === 'local') {
                // Get local timezone abbreviation
                const date = new Date();
                const tzString = date.toLocaleTimeString('en-US', { timeZoneName: 'short' }).split(' ')[2];
                return tzString || 'Local';
            } else if (this.timezone === 'UTC') {
                return 'UTC';
            } else {
                // Get timezone abbreviation for named timezone
                const date = new Date();
                const formatter = new Intl.DateTimeFormat('en-US', {
                    timeZone: this.timezone,
                    timeZoneName: 'short'
                });
                const parts = formatter.formatToParts(date);
                const tzPart = parts.find(part => part.type === 'timeZoneName');
                return tzPart ? tzPart.value : this.timezone;
            }
        },

        /**
         * Handle timezone change
         */
        onTimezoneChange() {
            // Update chart time scale format
            this.updateChartTimeScale();
            
            // Force the chart to redraw time axis by triggering a resize
            // This is needed to refresh the axis labels with new timezone
            if (this.chart) {
                const container = document.getElementById('chart-container');
                this.chart.resize(container.clientWidth, 400);
            }
            
            // Recalculate selection stats if active
            if (this.selectionStats) {
                this.calculateSelectionStats();
            }
            
            // Update last updated display
            this.lastUpdated = new Date().toLocaleTimeString(
                'en-US',
                { 
                    timeZone: this.timezone === 'local' ? undefined : this.timezone,
                    hour12: false 
                }
            ) + ` ${this.getTimezoneAbbr()}`;
        },

        /**
         * Update chart time scale configuration
         */
        updateChartTimeScale() {
            if (!this.chart) return;
            
            const self = this;
            
            // Update time scale to trigger a re-render with new timezone
            this.chart.applyOptions({
                timeScale: {
                    borderColor: '#333333',
                    timeVisible: true,
                    secondsVisible: false,
                    tickMarkFormatter: (time, tickMarkType, locale) => {
                        const date = new Date(time * 1000);
                        const tz = self.timezone === 'local' ? undefined : self.timezone;
                        
                        // Format based on tick mark type
                        if (tickMarkType === 0) { // Year
                            return date.toLocaleDateString('en-US', { year: 'numeric', timeZone: tz });
                        } else if (tickMarkType === 1) { // Month
                            return date.toLocaleDateString('en-US', { month: 'short', timeZone: tz });
                        } else if (tickMarkType === 2) { // Day
                            return date.toLocaleDateString('en-US', { day: 'numeric', timeZone: tz });
                        } else { // Time
                            return date.toLocaleTimeString('en-US', { 
                                hour: '2-digit', 
                                minute: '2-digit',
                                hour12: false,
                                timeZone: tz 
                            });
                        }
                    }
                },
                localization: {
                    timeFormatter: (timestamp) => {
                        return self.formatTimestamp(timestamp, {
                            year: undefined,
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: undefined
                        });
                    }
                }
            });
        },

        /**
         * Initialize Lightweight Charts
         */
        initChart() {
            console.log("initing chart")
            
            const container = document.getElementById('chart-container');
            
            const self = this;
            
            this.chart = LightweightCharts.createChart(container, {
                width: container.clientWidth,
                height: 400,
                layout: {
                    background: { color: '#1a1a1a' },
                    textColor: '#f5f5f5',
                },
                grid: {
                    vertLines: { color: '#262626' },
                    horzLines: { color: '#262626' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: '#333333',
                },
                timeScale: {
                    borderColor: '#333333',
                    timeVisible: true,
                    secondsVisible: false,
                    tickMarkFormatter: (time, tickMarkType, locale) => {
                        const date = new Date(time * 1000);
                        const tz = self.timezone === 'local' ? undefined : self.timezone;
                        
                        // Format based on tick mark type
                        if (tickMarkType === 0) { // Year
                            return date.toLocaleDateString('en-US', { year: 'numeric', timeZone: tz });
                        } else if (tickMarkType === 1) { // Month
                            return date.toLocaleDateString('en-US', { month: 'short', timeZone: tz });
                        } else if (tickMarkType === 2) { // Day
                            return date.toLocaleDateString('en-US', { day: 'numeric', timeZone: tz });
                        } else { // Time
                            return date.toLocaleTimeString('en-US', { 
                                hour: '2-digit', 
                                minute: '2-digit',
                                hour12: false,
                                timeZone: tz 
                            });
                        }
                    }
                },
                localization: {
                    timeFormatter: (timestamp) => {
                        return self.formatTimestamp(timestamp, {
                            year: undefined,
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: undefined
                        });
                    }
                }
            });

            this.candlestickSeries = this.chart.addCandlestickSeries({
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderVisible: false,
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
                priceFormat: {
                    type: 'price',
                    precision: 5,
                    minMove: 0.00001,
                },
                lastValueVisible: true,
                priceLineVisible: true,
            });

            // Handle window resize
            window.addEventListener('resize', () => {
                if (this.chart) {
                    this.chart.applyOptions({ 
                        width: container.clientWidth 
                    });
                }
            });

            // Listen for visible range changes (zoom/pan)
            this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                this.onVisibleRangeChange();
            });

            // Subscribe to click events for selection
            this.chart.subscribeClick((param) => {
                this.handleChartClick(param);
            });

            // Subscribe to crosshair move for selection preview and marker tooltips
            this.chart.subscribeCrosshairMove((param) => {
                this.handleCrosshairMove(param);
                this.handleMarkerHover(param);
            });

            // Create selection overlay element
            this.createSelectionOverlay();
            
            // Create marker tooltip element
            this.createMarkerTooltip();
        },

        /**
         * Create HTML overlay for selection visualization
         */
        createSelectionOverlay() {
            const container = document.getElementById('chart-container');
            this.selectionOverlay = document.createElement('div');
            this.selectionOverlay.className = 'selection-overlay';
            container.appendChild(this.selectionOverlay);
        },

        /**
         * Create marker tooltip element
         */
        createMarkerTooltip() {
            const container = document.getElementById('chart-container');
            this.markerTooltip = document.createElement('div');
            this.markerTooltip.className = 'marker-tooltip';
            this.markerTooltip.style.display = 'none';
            container.appendChild(this.markerTooltip);
        },

        /**
         * Handle marker hover to show tooltip
         */
        handleMarkerHover(param) {
            if (!this.markerTooltip || !param.time || this.markerData.size === 0) {
                if (this.markerTooltip) {
                    this.markerTooltip.style.display = 'none';
                }
                return;
            }

            const markerInfo = this.markerData.get(param.time);
            if (!markerInfo) {
                this.markerTooltip.style.display = 'none';
                return;
            }

            // Build tooltip content
            let content = '';
            if (markerInfo.buyCount > 0) {
                const avgBuyPrice = markerInfo.buyTao / markerInfo.buyAlpha;
                content += `<div class="marker-tooltip-buy">
                    <strong>${markerInfo.buyCount} Buy${markerInfo.buyCount > 1 ? 's' : ''}</strong><br>
                    ${markerInfo.buyAlpha.toFixed(2)}α / ${markerInfo.buyTao.toFixed(2)}τ<br>
                    Avg: ${avgBuyPrice.toFixed(5)} τ/α
                </div>`;
            }
            if (markerInfo.sellCount > 0) {
                if (content) content += '<div style="height: 8px;"></div>';
                const avgSellPrice = markerInfo.sellTao / markerInfo.sellAlpha;
                content += `<div class="marker-tooltip-sell">
                    <strong>${markerInfo.sellCount} Sell${markerInfo.sellCount > 1 ? 's' : ''}</strong><br>
                    ${markerInfo.sellAlpha.toFixed(2)}α / ${markerInfo.sellTao.toFixed(2)}τ<br>
                    Avg: ${avgSellPrice.toFixed(5)} τ/α
                </div>`;
            }

            this.markerTooltip.innerHTML = content;
            this.markerTooltip.style.display = 'block';

            // Position tooltip near cursor
            const coordinate = this.chart.timeScale().timeToCoordinate(param.time);
            if (coordinate !== null) {
                this.markerTooltip.style.left = coordinate + 'px';
                this.markerTooltip.style.top = '10px';
            }
        },

        /**
         * Handle chart click for time range selection
         */
        handleChartClick(param) {
            if (!param.time) return;

            if (!this.selectionActive) {
                // Start selection
                this.selectionStart = param.time;
                this.selectionEnd = null;
                this.selectionPreview = null;
                this.selectionActive = true;
                this.selectionStats = null;
                
                // Hide crosshair vertical line during selection
                this.chart.applyOptions({
                    crosshair: {
                        vertLine: {
                            visible: false
                        }
                    }
                });
                
                this.updateSelectionVisual();
            } else {
                // End selection
                this.selectionEnd = param.time;
                this.selectionPreview = null;
                this.selectionActive = false;
                
                // Show crosshair vertical line again
                this.chart.applyOptions({
                    crosshair: {
                        vertLine: {
                            visible: true,
                            labelVisible: true
                        }
                    }
                });
                
                this.updateSelectionVisual();
                this.calculateSelectionStats();
            }
        },

        /**
         * Handle crosshair movement for selection preview
         */
        handleCrosshairMove(param) {
            if (!this.selectionActive || !this.selectionStart) return;
            
            if (param.time) {
                this.selectionPreview = param.time;
                this.updateSelectionVisual();
            }
        },

        /**
         * Update visual representation of selection
         */
        updateSelectionVisual() {
            if (!this.selectionOverlay) return;

            if (!this.selectionStart) {
                this.selectionOverlay.style.display = 'none';
                return;
            }

            const end = this.selectionPreview || this.selectionEnd || this.selectionStart;
            const rangeStart = Math.min(this.selectionStart, end);
            const rangeEnd = Math.max(this.selectionStart, end);

            // Convert time to chart coordinates
            const timeScale = this.chart.timeScale();
            const startCoordinate = timeScale.timeToCoordinate(rangeStart);
            const endCoordinate = timeScale.timeToCoordinate(rangeEnd);

            if (startCoordinate === null || endCoordinate === null) {
                this.selectionOverlay.style.display = 'none';
                return;
            }

            // Calculate bar spacing to extend overlay to cover full candle widths
            const logicalRange = timeScale.getVisibleLogicalRange();
            if (!logicalRange) {
                this.selectionOverlay.style.display = 'none';
                return;
            }

            const barsInView = logicalRange.to - logicalRange.from;
            const chartWidth = this.chart.timeScale().width();
            const barSpacing = chartWidth / barsInView;
            const halfBarWidth = barSpacing / 2;

            // Extend to cover full candle widths
            const left = Math.min(startCoordinate, endCoordinate) - halfBarWidth;
            const right = Math.max(startCoordinate, endCoordinate) + halfBarWidth;
            const width = right - left;

            this.selectionOverlay.style.display = 'block';
            this.selectionOverlay.style.left = `${left}px`;
            this.selectionOverlay.style.width = `${width}px`;
        },

        /**
         * Calculate statistics for selected time range
         */
        calculateSelectionStats() {
            if (!this.selectionStart || !this.selectionEnd) return;

            const start = Math.min(this.selectionStart, this.selectionEnd);
            const end = Math.max(this.selectionStart, this.selectionEnd);

            // Filter candles within selected range
            const selectedCandles = Array.from(this.loadedData.values())
                .filter(c => c.time >= start && c.time <= end)
                .sort((a, b) => a.time - b.time);

            if (selectedCandles.length === 0) {
                this.selectionStats = null;
                return;
            }

            // Calculate proper time range:
            // Start: open_time of first candle
            // End: close_time of last candle (open_time + interval duration)
            const intervalSeconds = this.getIntervalSeconds();
            const rangeStartTime = selectedCandles[0].time;
            const rangeEndTime = selectedCandles[selectedCandles.length - 1].time + intervalSeconds;

            // Calculate stats
            const openPrice = selectedCandles[0].open;
            const closePrice = selectedCandles[selectedCandles.length - 1].close;
            const high = Math.max(...selectedCandles.map(c => c.high));
            const low = Math.min(...selectedCandles.map(c => c.low));
            const change = closePrice - openPrice;
            const changePercent = (change / openPrice) * 100;

            const tzAbbr = this.getTimezoneAbbr();
            this.selectionStats = {
                startTime: this.formatTimestamp(rangeStartTime) + ` ${tzAbbr}`,
                endTime: this.formatTimestamp(rangeEndTime) + ` ${tzAbbr}`,
                rangeStartTimestamp: rangeStartTime,  // Store for API calls
                rangeEndTimestamp: rangeEndTime,      // Store for API calls
                candles: selectedCandles.length,
                open: openPrice,
                close: closePrice,
                high: high,
                low: low,
                change: change,
                changePercent: changePercent,
            };
        },

        /**
         * Clear selection
         */
        clearSelection() {
            this.selectionStart = null;
            this.selectionEnd = null;
            this.selectionPreview = null;
            this.selectionActive = false;
            this.selectionStats = null;
            this.tradeData = null;
            
            // Show crosshair vertical line again
            this.chart.applyOptions({
                crosshair: {
                    vertLine: {
                        visible: true,
                        labelVisible: true
                    }
                }
            });
            
            if (this.selectionOverlay) {
                this.selectionOverlay.style.display = 'none';
            }
            
            // Re-render address trade markers if they exist
            if (this.addressTrades && this.addressTrades.length > 0) {
                this.renderTradeMarkers();
            }
        },

        /**
         * Fetch trade data for the selected time range
         */
        async fetchTradeData() {
            if (!this.selectionStats || !this.selectionStats.rangeStartTimestamp || !this.selectionStats.rangeEndTimestamp) {
                console.error('No selection range available');
                return;
            }

            this.loadingTrades = true;
            this.tradeData = null;

            try {
                const startTime = this.selectionStats.rangeStartTimestamp + 1; // +1 second not to include the candle that is being closed
                const endTime = this.selectionStats.rangeEndTimestamp;

                // Fetch both trade data and transfer data in parallel
                const [tradeResponse, transferResponse] = await Promise.all([
                    authFetch(`/api/v1/trade/range?netuid=${this.netuid}&start_time=${startTime}&end_time=${endTime}`),
                    authFetch(`/api/v1/transfer/range?netuid=${this.netuid}&start_time=${startTime}&end_time=${endTime}`)
                ]);

                if (!tradeResponse.ok) {
                    throw new Error(`Failed to fetch trade data: ${tradeResponse.statusText}`);
                }

                if (!transferResponse.ok) {
                    throw new Error(`Failed to fetch transfer data: ${transferResponse.statusText}`);
                }

                const tradeData = await tradeResponse.json();
                const transferData = await transferResponse.json();

                // Calculate aggregations
                const buyersAgg = this.calculateBuyersAggregation(tradeData.buyers || []);
                const sellersAgg = this.calculateSellersAggregation(tradeData.sellers || []);
                const gainersAgg = this.calculateGainersAggregation(tradeData.gainers || []);
                const losersAgg = this.calculateLosersAggregation(tradeData.losers || []);

                // Combine both datasets with aggregations
                this.tradeData = {
                    ...tradeData,
                    transfers: transferData.transfers || [],
                    buyersAgg,
                    sellersAgg,
                    gainersAgg,
                    losersAgg
                };

                console.log('Trade data loaded:', this.tradeData);
            } catch (err) {
                console.error('Error fetching trade data:', err);
                this.error = 'Failed to load trade data';
            } finally {
                this.loadingTrades = false;
            }
        },

        /**
         * Calculate aggregations for buyers
         */
        calculateBuyersAggregation(buyers) {
            if (!buyers || buyers.length === 0) {
                return { totalAlpha: 0, totalTao: 0, avgPrice: 0 };
            }

            const totalAlpha = buyers.reduce((sum, buyer) => sum + buyer.net_alpha, 0);
            const totalTao = buyers.reduce((sum, buyer) => sum + buyer.net_tao, 0);
            const avgPrice = totalAlpha > 0 ? totalTao / totalAlpha : 0;

            return { totalAlpha, totalTao, avgPrice };
        },

        /**
         * Calculate aggregations for sellers
         */
        calculateSellersAggregation(sellers) {
            if (!sellers || sellers.length === 0) {
                return { totalAlpha: 0, totalTao: 0, avgPrice: 0 };
            }

            const totalAlpha = sellers.reduce((sum, seller) => sum + seller.net_alpha, 0);
            const totalTao = sellers.reduce((sum, seller) => sum + seller.net_tao, 0);
            const avgPrice = totalAlpha > 0 ? totalTao / totalAlpha : 0;

            return { totalAlpha, totalTao, avgPrice };
        },

        /**
         * Calculate aggregations for gainers
         */
        calculateGainersAggregation(gainers) {
            if (!gainers || gainers.length === 0) {
                return { totalProfit: 0 };
            }

            const totalProfit = gainers.reduce((sum, gainer) => sum + gainer.tao_net, 0);

            return { totalProfit };
        },

        /**
         * Calculate aggregations for losers
         */
        calculateLosersAggregation(losers) {
            if (!losers || losers.length === 0) {
                return { totalLoss: 0 };
            }

            const totalLoss = losers.reduce((sum, loser) => sum + loser.tao_net, 0);

            return { totalLoss };
        },

        /**
         * Sort buyers table
         */
        sortBuyers(column) {
            if (this.buyersSortColumn === column) {
                this.buyersSortDirection = this.buyersSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.buyersSortColumn = column;
                this.buyersSortDirection = 'desc';
            }
        },

        /**
         * Sort sellers table
         */
        sortSellers(column) {
            if (this.sellersSortColumn === column) {
                this.sellersSortDirection = this.sellersSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.sellersSortColumn = column;
                this.sellersSortDirection = 'desc';
            }
        },

        /**
         * Sort gainers table
         */
        sortGainers(column) {
            if (this.gainersSortColumn === column) {
                this.gainersSortDirection = this.gainersSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.gainersSortColumn = column;
                this.gainersSortDirection = 'desc';
            }
        },

        /**
         * Sort losers table
         */
        sortLosers(column) {
            if (this.losersSortColumn === column) {
                this.losersSortDirection = this.losersSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.losersSortColumn = column;
                this.losersSortDirection = 'asc';
            }
        },

        /**
         * Get sorted buyers
         */
        get sortedBuyers() {
            if (!this.tradeData || !this.tradeData.buyers) return [];
            
            const buyers = [...this.tradeData.buyers];
            const column = this.buyersSortColumn;
            const direction = this.buyersSortDirection;
            
            return buyers.sort((a, b) => {
                let aVal, bVal;
                
                if (column === 'avg_buy') {
                    aVal = a.net_alpha > 0 ? a.net_tao / a.net_alpha : 0;
                    bVal = b.net_alpha > 0 ? b.net_tao / b.net_alpha : 0;
                } else if (column === 'upl') {
                    aVal = this.currentPrice ? (a.net_alpha * this.currentPrice - a.net_tao) : 0;
                    bVal = this.currentPrice ? (b.net_alpha * this.currentPrice - b.net_tao) : 0;
                } else {
                    aVal = a[column];
                    bVal = b[column];
                }
                
                if (direction === 'asc') {
                    return aVal > bVal ? 1 : -1;
                } else {
                    return aVal < bVal ? 1 : -1;
                }
            });
        },

        /**
         * Get sorted sellers
         */
        get sortedSellers() {
            if (!this.tradeData || !this.tradeData.sellers) return [];
            
            const sellers = [...this.tradeData.sellers];
            const column = this.sellersSortColumn;
            const direction = this.sellersSortDirection;
            
            return sellers.sort((a, b) => {
                let aVal, bVal;
                
                if (column === 'avg_sell') {
                    aVal = a.net_alpha > 0 ? a.net_tao / a.net_alpha : 0;
                    bVal = b.net_alpha > 0 ? b.net_tao / b.net_alpha : 0;
                } else {
                    aVal = a[column];
                    bVal = b[column];
                }
                
                if (direction === 'asc') {
                    return aVal > bVal ? 1 : -1;
                } else {
                    return aVal < bVal ? 1 : -1;
                }
            });
        },

        /**
         * Get sorted gainers
         */
        get sortedGainers() {
            if (!this.tradeData || !this.tradeData.gainers) return [];
            
            const gainers = [...this.tradeData.gainers];
            const column = this.gainersSortColumn;
            const direction = this.gainersSortDirection;
            
            return gainers.sort((a, b) => {
                let aVal, bVal;
                
                if (column === 'avg_buy') {
                    aVal = a.alpha_bought > 0 ? a.tao_bought / a.alpha_bought : 0;
                    bVal = b.alpha_bought > 0 ? b.tao_bought / b.alpha_bought : 0;
                } else if (column === 'avg_sell') {
                    aVal = a.alpha_sold > 0 ? a.tao_sold / a.alpha_sold : 0;
                    bVal = b.alpha_sold > 0 ? b.tao_sold / b.alpha_sold : 0;
                } else {
                    aVal = a[column];
                    bVal = b[column];
                }
                
                if (direction === 'asc') {
                    return aVal > bVal ? 1 : -1;
                } else {
                    return aVal < bVal ? 1 : -1;
                }
            });
        },

        /**
         * Get sorted losers
         */
        get sortedLosers() {
            if (!this.tradeData || !this.tradeData.losers) return [];
            
            const losers = [...this.tradeData.losers];
            const column = this.losersSortColumn;
            const direction = this.losersSortDirection;
            
            return losers.sort((a, b) => {
                let aVal, bVal;
                
                if (column === 'avg_buy') {
                    aVal = a.alpha_bought > 0 ? a.tao_bought / a.alpha_bought : 0;
                    bVal = b.alpha_bought > 0 ? b.tao_bought / b.alpha_bought : 0;
                } else if (column === 'avg_sell') {
                    aVal = a.alpha_sold > 0 ? a.tao_sold / a.alpha_sold : 0;
                    bVal = b.alpha_sold > 0 ? b.tao_sold / b.alpha_sold : 0;
                } else {
                    aVal = a[column];
                    bVal = b[column];
                }
                
                if (direction === 'asc') {
                    return aVal > bVal ? 1 : -1;
                } else {
                    return aVal < bVal ? 1 : -1;
                }
            });
        },

        /**
         * Get sort indicator for a column
         */
        getSortIndicator(table, column) {
            let sortColumn, sortDirection;
            
            if (table === 'buyers') {
                sortColumn = this.buyersSortColumn;
                sortDirection = this.buyersSortDirection;
            } else if (table === 'sellers') {
                sortColumn = this.sellersSortColumn;
                sortDirection = this.sellersSortDirection;
            } else if (table === 'gainers') {
                sortColumn = this.gainersSortColumn;
                sortDirection = this.gainersSortDirection;
            } else if (table === 'losers') {
                sortColumn = this.losersSortColumn;
                sortDirection = this.losersSortDirection;
            }
            
            if (sortColumn === column) {
                return sortDirection === 'asc' ? ' ▲' : ' ▼';
            }
            return '';
        },

        /**
         * Handle visible range changes with debouncing
         */
        onVisibleRangeChange() {
            // Update selection overlay position if active
            if (this.selectionStart || this.selectionEnd) {
                this.updateSelectionVisual();
            }

            // Don't trigger fetch if we're currently updating the chart
            if (this.isUpdatingChart) {
                return;
            }

            // Clear existing timer
            if (this.debounceTimer) {
                clearTimeout(this.debounceTimer);
            }

            // Debounce the data fetch (wait 500ms after user stops zooming/panning)
            this.debounceTimer = setTimeout(() => {
                this.loadDataForVisibleRange();
            }, 500);
        },

        /**
         * Load data for the currently visible time range
         */
        async loadDataForVisibleRange() {
            if (!this.chart || this.loading) return;

            const visibleRange = this.chart.timeScale().getVisibleRange();
            if (!visibleRange) return;

            // Add buffer to fetch slightly more data than visible (50% on each side)
            const timeSpan = visibleRange.to - visibleRange.from;
            const buffer = timeSpan * 0.5;
            
            const startTime = Math.floor(visibleRange.from - buffer);
            const endTime = Math.ceil(visibleRange.to + buffer);

            // Check what data we're missing
            const { needsFetch, fetchStart, fetchEnd } = this.getMissingDataRange(startTime, endTime);
            
            if (!needsFetch) {
                console.log('All data already loaded for visible range');
                return;
            }

            console.log(`Need to fetch data from ${new Date(fetchStart * 1000).toISOString()} to ${new Date(fetchEnd * 1000).toISOString()}`);
            
            // Fetch missing data
            await this.loadOHLCData(fetchStart, fetchEnd, false); // false = don't fit content
        },

        /**
         * Determine what data range is missing
         */
        getMissingDataRange(startTime, endTime) {
            if (this.loadedData.size === 0) {
                return { needsFetch: true, fetchStart: startTime, fetchEnd: endTime };
            }

            // Get all loaded timestamps
            const loadedTimes = Array.from(this.loadedData.keys()).sort((a, b) => a - b);
            const minLoaded = loadedTimes[0];
            const maxLoaded = loadedTimes[loadedTimes.length - 1];

            // Determine if we need to fetch data
            let needsFetch = false;
            let fetchStart = startTime;
            let fetchEnd = endTime;

            // Check if requested range is completely within loaded range
            if (startTime >= minLoaded && endTime <= maxLoaded) {
                // Check for gaps in the data
                const hasGaps = this.hasDataGaps(startTime, endTime);
                if (!hasGaps) {
                    return { needsFetch: false };
                }
            }

            // Need to fetch data outside the currently loaded range
            if (startTime < minLoaded) {
                needsFetch = true;
                fetchStart = startTime;
                fetchEnd = Math.min(endTime, minLoaded);
            } else if (endTime > maxLoaded) {
                needsFetch = true;
                fetchStart = Math.max(startTime, maxLoaded);
                fetchEnd = endTime;
            }

            return { needsFetch, fetchStart, fetchEnd };
        },

        /**
         * Check if there are gaps in loaded data for a time range
         */
        hasDataGaps(startTime, endTime) {
            // Get interval in seconds
            const intervalSeconds = this.getIntervalSeconds();
            
            // Check for missing data points
            for (let time = startTime; time <= endTime; time += intervalSeconds) {
                if (!this.loadedData.has(time)) {
                    return true;
                }
            }
            return false;
        },

        /**
         * Get interval duration in seconds
         */
        getIntervalSeconds() {
            const intervals = {
                '1m': 60,
                '5m': 300,
                '15m': 900,
                '30m': 1800,
                '1h': 3600,
                '1H': 3600,
                '4h': 14400,
                '4H': 14400,
                '1d': 86400,
                '1D': 86400,
                '7D': 604800,
            };
            return intervals[this.interval] || 3600;
        },

        /**
         * Calculate appropriate initial time range based on interval
         * Aims to show ~240 candles initially
         */
        getInitialTimeRange() {
            const targetCandles = 240; // Target number of candles to display
            const intervalSeconds = this.getIntervalSeconds();
            const timeSpan = targetCandles * intervalSeconds;
            
            const endTime = Math.floor(Date.now() / 1000);
            const startTime = endTime - timeSpan;
            
            return { startTime, endTime };
        },

        /**
         * Handle interval change - reset loaded range and reload data
         */
        async onIntervalChange() {
            // Stop current refresh timer
            this.stopPeriodicRefresh();
            
            // Clear all loaded data
            this.loadedData.clear();

            // Load data with dynamic time range based on new interval
            const { startTime, endTime } = this.getInitialTimeRange();
            await this.loadOHLCData(startTime, endTime, true); // true = fit content
            
            // Restart periodic refresh
            this.startPeriodicRefresh();
        },

        /**
         * Start periodic refresh timer
         */
        startPeriodicRefresh() {
            // Clear any existing timer
            this.stopPeriodicRefresh();
            
            // Set up new timer
            this.refreshTimer = setInterval(() => {
                this.refreshLatestCandle();
            }, this.refreshInterval);
            this.chartLive = true;

            console.log(`Started periodic refresh every ${this.refreshInterval}ms`);
        },

        /**
         * Stop periodic refresh timer
         */
        stopPeriodicRefresh() {
            if (this.refreshTimer) {
                clearInterval(this.refreshTimer);
                this.refreshTimer = null;
            }
            this.chartLive = false;
        },

        /**
         * Refresh the latest candle data
         */
        async refreshLatestCandle() {
            if (this.loading || this.isUpdatingChart) {
                return;
            }

            try {
                // Fetch last 2 candles to ensure we get the latest
                const now = Math.floor(Date.now() / 1000);
                const intervalSeconds = this.getIntervalSeconds();
                const startTime = now - (intervalSeconds * 2);
                
                const url = `/api/v1/ohlc/candles?netuid=${this.netuid}&interval=${this.interval}&start_time=${startTime}&end_time=${now}`;
                
                const response = await authFetch(url);
                
                if (!response.ok) {
                    console.error('Failed to refresh latest candle:', response.status);
                    return;
                }

                const data = await response.json();
                
                if (!data.candles || data.candles.length === 0) {
                    return;
                }

                this.isUpdatingChart = true;

                // Update loaded data with latest candles
                let hasUpdates = false;
                data.candles.forEach(candle => {
                    const chartCandle = {
                        time: candle.time,
                        open: parseFloat(candle.open) / 1e9,
                        high: parseFloat(candle.high) / 1e9,
                        low: parseFloat(candle.low) / 1e9,
                        close: parseFloat(candle.close) / 1e9,
                        volume: parseFloat(candle.volume) / 1e9,
                    };
                    
                    // Check if this is new or updated data
                    const existing = this.loadedData.get(candle.time);
                    if (!existing || 
                        existing.close !== chartCandle.close || 
                        existing.high !== chartCandle.high || 
                        existing.low !== chartCandle.low) {
                        this.loadedData.set(candle.time, chartCandle);
                        hasUpdates = true;
                    }
                });

                // Only update chart if there are actual changes
                if (hasUpdates) {
                    const allChartData = Array.from(this.loadedData.values())
                        .sort((a, b) => a.time - b.time);

                    this.candlestickSeries.setData(allChartData);
                    
                    // Re-render address trade markers if they exist
                    if (this.addressTrades && this.addressTrades.length > 0) {
                        this.renderTradeMarkers();
                    }

                    // Update stats
                    if (allChartData.length > 0) {
                        this.currentPrice = allChartData[allChartData.length - 1].close;
                    }
                    
                    this.lastUpdated = new Date().toLocaleTimeString(
                        'en-US',
                        { 
                            timeZone: this.timezone === 'local' ? undefined : this.timezone,
                            hour12: false 
                        }
                    ) + ` ${this.getTimezoneAbbr()}`;
                }

            } catch (err) {
                console.error('Error refreshing latest candle:', err);
            } finally {
                setTimeout(() => {
                    this.isUpdatingChart = false;
                }, 100);
            }
        },

        /**
         * Load OHLC data from API for a specific time range
         */
        async loadOHLCData(startTime, endTime, fitContent = false) {
            this.loading = true;
            this.error = null;
            this.isUpdatingChart = true; // Prevent event loop

            try {
                const url = `/api/v1/ohlc/candles?netuid=${this.netuid}&interval=${this.interval}&start_time=${startTime}&end_time=${endTime}`;
                
                console.log(`Fetching data from ${new Date(startTime * 1000).toISOString()} to ${new Date(endTime * 1000).toISOString()}`);
                
                const response = await authFetch(url);
                
                if (!response.ok) {
                    throw new Error(`API error: ${response.status}`);
                }

                const data = await response.json();
                
                if (!data.candles || data.candles.length === 0) {
                    if (this.loadedData.size === 0) {
                        this.error = 'No data available for this subnet';
                        this.candleCount = 0;
                    }
                    return;
                }

                // Transform and store data
                // Divide all prices by 10^9 for display
                data.candles.forEach(candle => {
                    const chartCandle = {
                        time: candle.time,
                        open: parseFloat(candle.open) / 1e9,
                        high: parseFloat(candle.high) / 1e9,
                        low: parseFloat(candle.low) / 1e9,
                        close: parseFloat(candle.close) / 1e9,
                        volume: parseFloat(candle.volume) / 1e9,
                    };
                    this.loadedData.set(candle.time, chartCandle);
                });

                // Get all data sorted by time
                const allChartData = Array.from(this.loadedData.values())
                    .sort((a, b) => a.time - b.time);

                // Update chart with all data
                this.candlestickSeries.setData(allChartData);
                
                // Re-render address trade markers if they exist
                if (this.addressTrades && this.addressTrades.length > 0) {
                    this.renderTradeMarkers();
                }
                
                // Fit content if requested (initial load or interval change)
                if (fitContent) {
                    this.chart.timeScale().fitContent();
                }

                // Calculate stats from latest data
                this.candleCount = allChartData.length;
                if (allChartData.length > 0) {
                    this.currentPrice = allChartData[allChartData.length - 1].close;
                }
                
                this.lastUpdated = new Date().toLocaleTimeString(
                    'en-US',
                    { 
                        timeZone: this.timezone === 'local' ? undefined : this.timezone,
                        hour12: false 
                    }
                ) + ` ${this.getTimezoneAbbr()}`;

            } catch (err) {
                console.error('Error loading OHLC data:', err);
                this.error = err.message;
            } finally {
                this.loading = false;
                // Delay resetting flag to ensure event doesn't trigger immediately
                setTimeout(() => {
                    this.isUpdatingChart = false;
                }, 100);
            }
        },

        /**
         * Get label for an address
         */
        getAddressLabel(address) {
            // Use cache for modal to ensure reactivity
            if (address === this.selectedAddress && this.modalLabelCache !== undefined) {
                return this.modalLabelCache;
            }
            return addressLabels.getLabel(address);
        },

        /**
         * Custom refresh function for label updates (called by shared helper)
         */
        refreshDataForLabelUpdate() {
            // Force re-render to show updated label in trader lists
            if (this.tradeData) {
                this.tradeData = { ...this.tradeData };
            }
        },

        /**
         * Load trading activity for a specific address
         */
        async loadAddressTrades() {
            const address = this.addressFilter.trim();
            
            if (!address) {
                alert('Please enter an address');
                return;
            }

            this.loadingAddressTrades = true;
            this.addressTrades = null;

            try {
                const url = `/api/v1/trade/address?netuid=${this.netuid}&address=${encodeURIComponent(address)}`;
                const response = await authFetch(url);

                if (!response.ok) {
                    throw new Error(`Failed to fetch address trades: ${response.statusText}`);
                }

                const data = await response.json();
                this.addressTrades = data.trades || [];

                console.log('Address trades:', this.addressTrades);

                console.log(`Loaded ${this.addressTrades.length} trades for address ${address}`);

                // Render markers on chart
                this.renderTradeMarkers();

            } catch (err) {
                console.error('Error fetching address trades:', err);
                alert('Failed to load address trades. Please check the address and try again.');
            } finally {
                this.loadingAddressTrades = false;
            }
        },

        /**
         * Clear address trades and markers
         */
        clearAddressTrades() {
            this.addressFilter = '';
            this.addressTrades = null;
            this.tradeMarkers = [];
            this.markerData.clear();
            
            // Clear markers from chart
            if (this.candlestickSeries) {
                this.candlestickSeries.setMarkers([]);
            }
            
            // Hide tooltip
            if (this.markerTooltip) {
                this.markerTooltip.style.display = 'none';
            }
        },

        /**
         * Render trade markers on the chart
         */
        renderTradeMarkers() {
            // Clear markers if no trades
            if (!this.addressTrades || this.addressTrades.length === 0) {
                this.candlestickSeries.setMarkers([]);
                this.markerData.clear();
                if (this.markerTooltip) {
                    this.markerTooltip.style.display = 'none';
                }
                return;
            }

            // Get visible range to only show markers in viewport
            const visibleRange = this.chart.timeScale().getVisibleRange();
            if (!visibleRange) {
                return;
            }

            // Get interval in seconds to determine candle boundaries
            const intervalSeconds = this.getIntervalSeconds();

            // Group trades by candle time and calculate totals
            const tradesByCandle = new Map();
            
            this.addressTrades.forEach(trade => {
                // Calculate which candle this trade belongs to
                const candleTime = Math.floor((trade.timestamp - 1) / intervalSeconds) * intervalSeconds;
                
                // Only include trades within visible range
                if (candleTime < visibleRange.from || candleTime > visibleRange.to) {
                    return;
                }
                
                if (!tradesByCandle.has(candleTime)) {
                    tradesByCandle.set(candleTime, { 
                        buyCount: 0, 
                        sellCount: 0,
                        buyAlpha: 0,
                        buyTao: 0,
                        sellAlpha: 0,
                        sellTao: 0
                    });
                }
                
                const candle = tradesByCandle.get(candleTime);
                if (trade.is_sell) {
                    candle.sellCount++;
                    candle.sellAlpha += trade.alpha;
                    candle.sellTao += trade.tao;
                } else {
                    candle.buyCount++;
                    candle.buyAlpha += trade.alpha;
                    candle.buyTao += trade.tao;
                }
            });

            // Store marker data for tooltips and create markers
            this.markerData.clear();
            const markers = [];
            
            tradesByCandle.forEach((data, candleTime) => {
                // Store data for tooltip
                this.markerData.set(candleTime, data);
                
                // Add buy marker if there are buys (no text)
                if (data.buyCount > 0) {
                    markers.push({
                        time: candleTime,
                        position: 'belowBar',
                        color: '#00E5FF',
                        shape: 'arrowUp'
                    });
                }

                // Add sell marker if there are sells (no text)
                if (data.sellCount > 0) {
                    markers.push({
                        time: candleTime,
                        position: 'aboveBar',
                        color: '#FF6D00',
                        shape: 'arrowDown'
                    });
                }
            });

            // Sort markers by time
            markers.sort((a, b) => a.time - b.time);

            // Apply markers to the series using the setMarkers method
            this.candlestickSeries.setMarkers(markers);
            
            this.tradeMarkers = markers;
            
            console.log(`Rendered ${markers.length} markers for ${this.addressTrades.length} trades across ${tradesByCandle.size} candles`);
        },

        /**
         * Navigate to another subnet
         */
        goToSubnet() {
            const subnetNum = parseInt(this.subnetInput);
            if (!isNaN(subnetNum) && subnetNum >= 0 && subnetNum <= 1024) {
                window.location.href = `/subnet/${subnetNum}`;
            } else {
                alert('Please enter a valid subnet number (0-1024)');
            }
        },
    };
}

