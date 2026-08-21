/**
 * Transfer Connections Page
 * Handles network graph visualization of transfer data
 */

function transfersApp() {
    return {
        // Spread shared address modal helpers
        ...addressModalHelpers,

        // State
        searchAddress: '',
        addressInput: '',
        currentAddress: null,
        loading: false,
        error: null,
        hasData: false,

        // Data
        transfers: [],

        // Statistics
        stats: {
            totalConnections: 0,
            uniqueAddresses: 0,
            totalTransfers: 0,
            totalVolume: 0
        },

        // Network graph
        network: null,
        nodes: null,
        edges: null,

        // Node selection popup
        selectedNode: null,
        popupPosition: { x: 0, y: 0 },
        loadingExpand: false,

        // Track expanded addresses
        expandedAddresses: new Set(), // Track which addresses have been expanded

        // Edge transfers modal
        edgeTransfersModalOpen: false,
        selectedEdge: null,
        edgeTransfers: [],
        loadingEdgeTransfers: false,
        edgeTransfersError: null,

        // Graph search
        graphSearchQuery: '',
        graphSearchResult: null,
        highlightInterval: null,

        // Expand address
        expandAddressQuery: '',

        // Save/Load graphs
        saveLoadModalOpen: false,
        saveGraphName: '',
        savedGraphs: [],
        savingGraph: false,
        currentLoadedGraphId: null, // Track which graph is currently loaded
        currentLoadedGraphName: null,

        /**
         * Initialize the app
         */
        async init() {
            // Check if we should load a saved graph from sessionStorage
            const loadGraphId = sessionStorage.getItem('loadGraphId');
            if (loadGraphId) {
                // Clear the flag
                sessionStorage.removeItem('loadGraphId');
                
                // Load the saved graph (skip confirmation)
                await this.loadSavedGraph(loadGraphId, true);
                return;
            }

            // Extract address from URL path: /connections/{address}
            const pathParts = window.location.pathname.split('/');
            const addressParam = pathParts.length >= 3 ? pathParts[2] : null;

            if (addressParam && addressParam.length >= 47) {
                this.searchAddress = addressParam;
                this.expandedAddresses.add(addressParam); // Mark initial address as expanded
                
                // Update page title with first 5 characters of address
                const addressPrefix = addressParam.substring(0, 5);
                document.title = `${addressPrefix} | AlphaStats`;
                
                await this.loadAddress();
            } else {
                // No valid address provided, redirect to home
                window.location.href = '/';
            }
        },

        /**
         * Search for transfers by address (called from form submission)
         */
        async searchAddress() {
            if (!this.searchAddress || this.searchAddress.length < 47) {
                this.error = 'Please enter a valid SS58 address (47-48 characters)';
                return;
            }

            // Navigate to the address-specific URL
            window.location.href = `/connections/${this.searchAddress}`;
        },

        /**
         * Load transfers by address (called on page load)
         */
        async loadAddress() {
            if (!this.searchAddress || this.searchAddress.length < 47) {
                this.error = 'Please enter a valid SS58 address (47-48 characters)';
                return;
            }

            this.loading = true;
            this.error = null;
            this.hasData = false;
            this.currentAddress = this.searchAddress;
            
            // Clear loaded graph tracking (fresh load)
            this.currentLoadedGraphId = null;
            this.currentLoadedGraphName = null;

            try {
                // Fetch transfer data from API
                const response = await authFetch(
                    `/api/v1/transfer/${this.searchAddress}?limit=1000`
                );

                if (!response.ok) {
                    throw new Error(`API Error: ${response.status} ${response.statusText}`);
                }

                const data = await response.json();

                if (!data.transfers || data.transfers.length === 0) {
                    this.error = 'No transfers found for this address';
                    this.loading = false;
                    return;
                }

                this.transfers = data.transfers;
                this.hasData = true;

                // Calculate statistics
                this.calculateStats();

                // Build and render the network graph
                await this.$nextTick(); // Wait for DOM to update
                this.buildNetworkGraph();

            } catch (err) {
                console.error('Error fetching transfer data:', err);
                this.error = `Failed to load transfer data: ${err.message}`;
            } finally {
                this.loading = false;
            }
        },

        /**
         * Calculate statistics from transfer data
         */
        calculateStats() {
            const uniqueAddrs = new Set();
            let totalTransfers = 0;
            let totalVolume = 0;

            this.transfers.forEach(transfer => {
                uniqueAddrs.add(transfer.from_address);
                uniqueAddrs.add(transfer.to_address);
                totalTransfers += transfer.transfer_count;
                totalVolume += transfer.total_tao_amount;
            });

            // Remove the queried address from unique count
            uniqueAddrs.delete(this.currentAddress);

            this.stats = {
                totalConnections: this.transfers.length,
                uniqueAddresses: uniqueAddrs.size,
                totalTransfers: totalTransfers,
                totalVolume: totalVolume.toFixed(2)
            };
        },

        /**
         * Build and render the network graph using vis-network
         */
        buildNetworkGraph() {
            const container = document.getElementById('network-container');

            if (!container) {
                console.error('Network container not found');
                return;
            }

            // Prepare nodes and edges
            const nodesMap = new Map();
            const edgesMap = new Map(); // Use Map to track edges by node pairs

            // Add the queried address as the central node
            const centralHasLabel = !!addressLabels.getLabel(this.currentAddress);
            nodesMap.set(this.currentAddress, {
                id: this.currentAddress,
                label: this.formatNodeLabel(this.currentAddress),
                color: {
                    background: '#3b82f6',
                    border: '#2563eb',
                    highlight: {
                        background: '#2563eb',
                        border: '#1d4ed8'
                    }
                },
                font: {
                    color: centralHasLabel ? '#10b981' : '#ffffff',
                    size: 14,
                    face: 'monospace',
                    bold: true,
                    background: 'rgba(0, 0, 0, 0.7)',
                    strokeWidth: 0
                },
                size: 20,
                borderWidth: 1,
                shape: 'dot',
                fixed: { x: false, y: false }
            });

            // Track address roles (sender, receiver, or both)
            const addressRoles = new Map();

            // Process transfers to create nodes and edges
            this.transfers.forEach(transfer => {
                const fromAddr = transfer.from_address;
                const toAddr = transfer.to_address;
                const otherAddr = fromAddr === this.currentAddress ? toAddr : fromAddr;

                // Track roles
                if (!addressRoles.has(otherAddr)) {
                    addressRoles.set(otherAddr, { sender: false, receiver: false });
                }

                if (transfer.direction === 'received') {
                    // Current address received from otherAddr (otherAddr is sender)
                    addressRoles.get(otherAddr).sender = true;
                } else {
                    // Current address sent to otherAddr (otherAddr is receiver)
                    addressRoles.get(otherAddr).receiver = true;
                }

                // Add the other address as a node if not already added
                if (!nodesMap.has(otherAddr)) {
                    const role = addressRoles.get(otherAddr);
                    const isExpanded = this.expandedAddresses.has(otherAddr);
                    const color = this.getNodeColor(role, isExpanded);
                    const hasLabel = !!addressLabels.getLabel(otherAddr);

                    nodesMap.set(otherAddr, {
                        id: otherAddr,
                        label: this.formatNodeLabel(otherAddr),
                        color: color,
                        font: {
                            color: hasLabel ? '#10b981' : '#ffffff',
                            size: 12,
                            face: 'monospace',
                            background: 'rgba(0, 0, 0, 0.7)',
                            strokeWidth: 0
                        },
                        size: 15,
                        borderWidth: 1,
                        shape: 'dot'
                    });
                }

                // Create or update edge (merge bidirectional edges)
                // Use sorted addresses as key to identify the same connection
                const edgeKey = [fromAddr, toAddr].sort().join('-');

                if (!edgesMap.has(edgeKey)) {
                    // Create new edge
                    edgesMap.set(edgeKey, {
                        id: edgeKey,
                        from: fromAddr,
                        to: toAddr,
                        arrows: 'to',
                        bidirectional: false
                    });
                } else {
                    // Edge exists - check if it's opposite direction
                    const existingEdge = edgesMap.get(edgeKey);
                    if (existingEdge.from !== fromAddr || existingEdge.to !== toAddr) {
                        // Opposite direction exists, make it bidirectional
                        existingEdge.arrows = 'to, from';
                        existingEdge.bidirectional = true;
                    }
                }
            });

            // Convert edges map to array
            const edgesArray = Array.from(edgesMap.values()).map(edge => ({
                id: edge.id,
                from: edge.from,
                to: edge.to,
                arrows: edge.arrows,
                color: {
                    color: edge.bidirectional ? '#10b981' : '#6366f1', // Green for bidirectional, purple for unidirectional
                    highlight: edge.bidirectional ? '#059669' : '#4f46e5'
                },
                width: 1,
                smooth: false // Straight edges
            }));

            // Update node colors based on final roles and expansion status
            nodesMap.forEach((node, addr) => {
                if (addr !== this.currentAddress) {
                    const role = addressRoles.get(addr);
                    const isExpanded = this.expandedAddresses.has(addr);
                    node.color = this.getNodeColor(role, isExpanded);
                }
            });

            // Create vis-network dataset
            const nodes = new vis.DataSet(Array.from(nodesMap.values()));
            const edges = new vis.DataSet(edgesArray);

            // Network options
            const options = {
                nodes: {
                    borderWidth: 1,
                    borderWidthSelected: 3,
                    shadow: {
                        enabled: true,
                        color: 'rgba(0,0,0,0.5)',
                        size: 6,
                        x: 0,
                        y: 0
                    }
                },
                "edges": {
                    "smooth": {
                        "forceDirection": "none",
                        "roundness": 0.6
                    },
                    arrows: {
                        to: {
                            enabled: true,
                            scaleFactor: 0.3
                        },
                        from: {
                            enabled: true,
                            scaleFactor: 0.3
                        }
                    },
                },
                "physics": {
                    "forceAtlas2Based": {
                        "springLength": 100
                    },
                    "maxVelocity": 8,
                    "minVelocity": 0.34,
                    "solver": "forceAtlas2Based"
                },
                interaction: {
                    hover: true,
                    tooltipDelay: 200,
                    zoomView: true,
                    dragView: true
                },
                // layout: {
                //     improvedLayout: true,
                //     hierarchical: false
                // }
            };

            // Create or update network
            if (this.network) {
                this.network.destroy();
            }

            this.network = new vis.Network(container, { nodes, edges }, options);

            // Add click event to show popup
            this.network.on('click', (params) => {
                if (params.nodes.length > 0) {
                    // Node clicked
                    const nodeId = params.nodes[0];
                    this.showNodePopup(nodeId, params.pointer.DOM);
                } else if (params.edges.length > 0) {
                    // Edge clicked
                    const edgeId = params.edges[0];
                    this.handleEdgeClick(edgeId);
                    this.selectedNode = null; // Close node popup
                } else {
                    // Click on empty space - close popup
                    this.selectedNode = null;
                }
            });

            // Store references
            this.nodes = nodes;
            this.edges = edges;
        },

        /**
         * Get node color based on expansion status
         */
        getNodeColor(role, isExpanded = false) {
            if (isExpanded) {
                // Grey color for expanded nodes
                return {
                    background: '#94a3b8',
                    border: '#64748b',
                    highlight: {
                        background: '#64748b',
                        border: '#475569'
                    }
                };
            }
            // Purple color for unexpanded nodes
            return {
                background: '#6366f1',
                border: '#4f46e5',
                highlight: {
                    background: '#4f46e5',
                    border: '#4338ca'
                }
            };
        },

        /**
         * Format address for display
         */
        formatAddress(address) {
            if (!address) return '';
            return address.substring(0, 6) + '...' + address.substring(address.length - 4);
        },

        /**
         * Format node label with custom label if available
         */
        formatNodeLabel(address) {
            if (!address) return '';
            
            const customLabel = addressLabels.getLabel(address);
            const shortAddr = this.formatAddress(address);
            
            if (customLabel) {
                // Show custom label on first line, address on second line
                return `${customLabel}\n${shortAddr}`;
            }
            
            return shortAddr;
        },

        /**
         * Show popup for node actions
         */
        showNodePopup(address, position) {
            this.selectedNode = address;
            // Position popup near the click
            const container = document.getElementById('network-container');
            const rect = container.getBoundingClientRect();

            this.popupPosition = {
                x: Math.min(position.x, rect.width - 270), // Keep popup in bounds
                y: Math.min(position.y, rect.height - 150)
            };
        },

        /**
         * Remove a node and all its connected edges from the graph
         */
        removeNode(address) {
            if (!address) {
                return;
            }

            // Prevent removing the root/current address
            if (address === this.currentAddress) {
                alert('Cannot remove the root address node');
                this.selectedNode = null;
                return;
            }

            if (!this.nodes || !this.edges) {
                alert('No graph loaded');
                return;
            }

            // Confirm removal
            const label = this.getAddressLabel(address);
            const displayName = label ? `${label} (${this.formatAddress(address)})` : this.formatAddress(address);
            
            if (!confirm(`Remove node "${displayName}"?\n\nThis will also remove all connected edges and orphaned nodes.`)) {
                return;
            }

            try {
                // Find all edges connected to this node
                const allEdges = this.edges.get();
                const edgesToRemove = allEdges.filter(edge => 
                    edge.from === address || edge.to === address
                ).map(edge => edge.id);

                // Remove the edges
                if (edgesToRemove.length > 0) {
                    this.edges.remove(edgesToRemove);
                }

                // Remove the node
                this.nodes.remove(address);

                // Remove from expanded addresses set
                this.expandedAddresses.delete(address);

                // Find and remove orphan nodes (nodes with no edges)
                const remainingEdges = this.edges.get();
                const nodesWithEdges = new Set();
                
                // Collect all nodes that have at least one edge
                remainingEdges.forEach(edge => {
                    nodesWithEdges.add(edge.from);
                    nodesWithEdges.add(edge.to);
                });

                // Find orphan nodes (nodes without any edges)
                const allNodes = this.nodes.get();
                const orphanNodes = allNodes.filter(node => 
                    node.id !== this.currentAddress && !nodesWithEdges.has(node.id)
                );

                // Remove orphan nodes
                if (orphanNodes.length > 0) {
                    const orphanIds = orphanNodes.map(node => node.id);
                    this.nodes.remove(orphanIds);
                    
                    // Remove from expanded addresses set
                    orphanIds.forEach(id => {
                        this.expandedAddresses.delete(id);
                    });
                    
                    console.log(`Removed ${orphanNodes.length} orphan node(s)`);
                }

                // Update stats
                this.stats.totalConnections = this.edges.length;
                const uniqueAddrs = new Set();
                this.nodes.forEach(node => {
                    if (node.id !== this.currentAddress) {
                        uniqueAddrs.add(node.id);
                    }
                });
                this.stats.uniqueAddresses = uniqueAddrs.size;

                // Close popup
                this.selectedNode = null;

                // Show success message
                console.log(`Removed node ${address}, ${edgesToRemove.length} connected edge(s), and ${orphanNodes.length} orphan node(s)`);
            } catch (error) {
                console.error('Error removing node:', error);
                alert('Failed to remove node');
            }
        },

        /**
         * Expand node by fetching its transfer data
         */
        async expandNode(address) {
            if (!address) {
                return;
            }
            
            // Check if node exists in graph
            const nodeExists = this.nodes.get(address) !== null;

            this.loadingExpand = true;

            try {
                // Fetch transfer data for the selected address
                const response = await authFetch(`/api/v1/transfer/${address}?limit=1000`);

                if (!response.ok) {
                    throw new Error(`API Error: ${response.status}`);
                }

                const data = await response.json();

                if (!data.transfers || data.transfers.length === 0) {
                    alert('No transfers found for this address');
                    return;
                }

                // Check if connections are too large and ask for confirmation
                if (data.transfers.length > 100) {
                    const confirmed = confirm(
                        `This address has ${data.transfers.length} connections.\n\n` +
                        `Expanding it may make the graph cluttered and slow.\n\n` +
                        `Do you want to proceed?`
                    );
                    
                    if (!confirmed) {
                        // User cancelled, don't expand
                        return;
                    }
                }

                // Mark as expanded
                this.expandedAddresses.add(address);

                // Add the node to graph if it doesn't exist yet
                if (!nodeExists) {
                    const hasLabel = !!addressLabels.getLabel(address);
                    try {
                        this.nodes.add({
                            id: address,
                            label: this.formatNodeLabel(address),
                            color: this.getNodeColor({}, true), // Grey for expanded
                            font: {
                                color: hasLabel ? '#10b981' : '#ffffff',
                                size: 12,
                                face: 'monospace',
                                background: 'rgba(0, 0, 0, 0.7)',
                                strokeWidth: 0
                            },
                            size: 15,
                            borderWidth: 1,
                            shape: 'dot'
                        });
                    } catch (err) {
                        console.error('Error adding new node:', err);
                    }
                } else {
                    // Update the expanded node to grey color
                    this.nodes.update({
                        id: address,
                        color: this.getNodeColor({}, true) // Mark as expanded (grey)
                    });
                }

                // Add new nodes and edges to existing graph
                const edgesToAdd = new Map();

                data.transfers.forEach(transfer => {
                    const fromAddr = transfer.from_address;
                    const toAddr = transfer.to_address;
                    const otherAddr = fromAddr === address ? toAddr : fromAddr;

                    // Add new node if it doesn't exist
                    try {
                        if (!this.nodes.get(otherAddr)) {
                            const isOtherExpanded = this.expandedAddresses.has(otherAddr);
                            const hasLabel = !!addressLabels.getLabel(otherAddr);
                            this.nodes.add({
                                id: otherAddr,
                                label: this.formatNodeLabel(otherAddr),
                                color: this.getNodeColor({}, isOtherExpanded),
                                font: {
                                    color: hasLabel ? '#10b981' : '#ffffff',
                                    size: 12,
                                    face: 'monospace',
                                    background: 'rgba(0, 0, 0, 0.7)',
                                    strokeWidth: 0
                                },
                                size: 15,
                                borderWidth: 1,
                                shape: 'dot'
                            });
                        }
                    } catch (err) {
                        // Node might already exist, skip
                        console.debug('Node already exists:', otherAddr);
                    }

                    // Track edges to merge bidirectional ones
                    const edgeKey = [fromAddr, toAddr].sort().join('-');
                    const existingEdge = this.edges.get(edgeKey);

                    if (!existingEdge) {
                        // Edge doesn't exist in graph, check if we're building it
                        if (!edgesToAdd.has(edgeKey)) {
                            edgesToAdd.set(edgeKey, {
                                id: edgeKey,
                                from: fromAddr,
                                to: toAddr,
                                arrows: 'to',
                                bidirectional: false
                            });
                        } else {
                            // Opposite direction exists in new edges
                            const pendingEdge = edgesToAdd.get(edgeKey);
                            if (pendingEdge.from !== fromAddr || pendingEdge.to !== toAddr) {
                                pendingEdge.arrows = 'to, from';
                                pendingEdge.bidirectional = true;
                            }
                        }
                    } else {
                        // Edge exists, check if we need to make it bidirectional
                        if (existingEdge.from !== fromAddr || existingEdge.to !== toAddr) {
                            // Opposite direction, update to bidirectional
                            this.edges.update({
                                id: edgeKey,
                                arrows: 'to, from',
                                color: {
                                    color: '#10b981', // Green for bidirectional
                                    highlight: '#059669'
                                }
                            });
                        }
                        // If same direction, edge already exists, no need to add
                    }
                });

                // Add new edges to graph
                edgesToAdd.forEach(edge => {
                    try {
                        this.edges.add({
                            id: edge.id,
                            from: edge.from,
                            to: edge.to,
                            arrows: edge.arrows,
                            color: {
                                color: edge.bidirectional ? '#10b981' : '#6366f1', // Green for bidirectional, purple for unidirectional
                                highlight: edge.bidirectional ? '#059669' : '#4f46e5'
                            },
                            width: 1,
                            smooth: false // Straight edges
                        });
                    } catch (err) {
                        // Edge might already exist due to race condition, skip
                        console.debug('Edge already exists:', edge.id);
                    }
                });

                // Update stats
                this.stats.totalConnections = this.edges.length;
                const uniqueAddrs = new Set();
                this.nodes.forEach(node => {
                    if (node.id !== this.currentAddress) {
                        uniqueAddrs.add(node.id);
                    }
                });
                this.stats.uniqueAddresses = uniqueAddrs.size;

            } catch (err) {
                console.error('Error expanding node:', err);
                alert(`Failed to expand node: ${err.message}`);
            } finally {
                this.loadingExpand = false;
                this.selectedNode = null;
            }
        },

        /**
         * Go to connections page for selected address
         */
        goToConnections(address) {
            if (address) {
                window.open(`/connections/${address}`, '_blank');
            }
            this.selectedNode = null;
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
         * Override openAddressModal to close node popup
         */
        openAddressModal(address) {
            // Call shared implementation
            addressModalHelpers.openAddressModal.call(this, address);
            // Close node popup
            this.selectedNode = null;
        },

        /**
         * Custom refresh function for label updates (called by shared helper)
         */
        refreshDataForLabelUpdate() {
            // Update network if needed
            if (this.network) {
                this.updateNetworkLabels();
            }
            
            // Force re-render of transfers table
            this.transfers = [...this.transfers];
        },

        /**
         * Update network node labels
         */
        updateNetworkLabels() {
            if (!this.network) return;

            const nodes = this.network.body.data.nodes;
            const updatedNodes = nodes.get().map(node => {
                const hasLabel = !!addressLabels.getLabel(node.id);
                return {
                    id: node.id,
                    label: this.formatNodeLabel(node.id),
                    font: {
                        color: hasLabel ? '#10b981' : '#ffffff',
                        size: node.id === this.currentAddress ? 14 : 12,
                        face: 'monospace',
                        bold: node.id === this.currentAddress,
                        background: 'rgba(0, 0, 0, 0.7)',
                        strokeWidth: 0
                    }
                };
            });

            nodes.update(updatedNodes);
        },

        /**
         * Handle edge click
         */
        handleEdgeClick(edgeId) {
            // Get edge data
            const edge = this.network.body.data.edges.get(edgeId);
            if (!edge) return;

            this.selectedEdge = {
                from: edge.from,
                to: edge.to
            };

            this.openEdgeTransfersModal();
        },

        /**
         * Open edge transfers modal
         */
        openEdgeTransfersModal() {
            this.edgeTransfersModalOpen = true;
            this.edgeTransfers = [];
            this.edgeTransfersError = null;
            this.fetchEdgeTransfers();
        },

        /**
         * View transaction details from table button
         */
        viewTransactionDetails(fromAddress, toAddress) {
            this.selectedEdge = {
                from: fromAddress,
                to: toAddress
            };
            this.openEdgeTransfersModal();
        },

        /**
         * Close edge transfers modal
         */
        closeEdgeTransfersModal() {
            this.edgeTransfersModalOpen = false;
            this.selectedEdge = null;
            this.edgeTransfers = [];
            this.edgeTransfersError = null;
        },

        /**
         * Fetch transfers between two addresses
         */
        async fetchEdgeTransfers() {
            if (!this.selectedEdge) return;

            this.loadingEdgeTransfers = true;
            this.edgeTransfersError = null;

            try {
                const response = await authFetch(
                    `/api/v1/transfer/between/${this.selectedEdge.from}/${this.selectedEdge.to}?limit=100`
                );

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                this.edgeTransfers = data.transfers || [];
            } catch (error) {
                console.error('Error fetching edge transfers:', error);
                this.edgeTransfersError = 'Failed to load transfers. Please try again.';
            } finally {
                this.loadingEdgeTransfers = false;
            }
        },

        /**
         * Format timestamp for display
         */
        formatTimestamp(timestamp) {
            const date = new Date(timestamp * 1000);
            return date.toLocaleString();
        },

        /**
         * Search for an address in the graph and highlight it
         */
        searchInGraph() {
            const query = this.graphSearchQuery.trim();
            
            if (!query) {
                this.graphSearchResult = {
                    found: false,
                    message: 'Please enter an address to search'
                };
                return;
            }

            if (!this.network || !this.nodes) {
                this.graphSearchResult = {
                    found: false,
                    message: 'Graph not loaded'
                };
                return;
            }

            // Search for exact match only
            const node = this.nodes.get(query);
            
            if (!node) {
                this.graphSearchResult = {
                    found: false,
                    message: 'Address not found in the graph.'
                };
                
                // Auto-clear error message after 3 seconds
                setTimeout(() => {
                    this.graphSearchResult = null;
                }, 3000);
                return;
            }

            // Highlight the found node
            this.highlightNode(query);
            
            const label = addressLabels.getLabel(query);
            this.graphSearchResult = {
                found: true,
                message: `Found: ${this.formatAddress(query)}${label ? ' (' + label + ')' : ''}`
            };

            // Auto-clear result message after 5 seconds
            setTimeout(() => {
                this.graphSearchResult = null;
            }, 5000);
        },

        /**
         * Highlight a node in the graph
         */
        highlightNode(nodeId) {
            if (!this.network) return;

            // Clear any previous highlighting intervals
            if (this.highlightInterval) {
                clearInterval(this.highlightInterval);
                this.highlightInterval = null;
            }

            // Get the original node properties
            const originalNode = this.nodes.get(nodeId);
            if (!originalNode) return;

            // Store original properties
            const originalSize = originalNode.size || 15;
            const originalBorderWidth = originalNode.borderWidth || 1;
            const originalColor = originalNode.color;

            // Focus on the node with animation
            this.network.focus(nodeId, {
                scale: 2.0,
                animation: {
                    duration: 800,
                    easingFunction: 'easeInOutQuad'
                }
            });

            // Select the node
            this.network.selectNodes([nodeId]);

            // Create a more visible pulsing effect with yellow highlight
            let pulseCount = 0;
            const maxPulses = 8;
            
            this.highlightInterval = setInterval(() => {
                if (pulseCount >= maxPulses) {
                    clearInterval(this.highlightInterval);
                    this.highlightInterval = null;
                    
                    // Restore original appearance
                    this.nodes.update({
                        id: nodeId,
                        size: originalSize,
                        borderWidth: originalBorderWidth,
                        color: originalColor
                    });
                    return;
                }
                
                const isHighlight = pulseCount % 2 === 0;
                
                if (isHighlight) {
                    // Highlight state - bright yellow/gold
                    this.nodes.update({
                        id: nodeId,
                        size: originalSize * 1.8,
                        borderWidth: 6,
                        color: {
                            background: '#fbbf24',
                            border: '#f59e0b',
                            highlight: {
                                background: '#f59e0b',
                                border: '#d97706'
                            }
                        }
                    });
                } else {
                    // Normal state
                    this.nodes.update({
                        id: nodeId,
                        size: originalSize,
                        borderWidth: originalBorderWidth,
                        color: originalColor
                    });
                }
                
                pulseCount++;
            }, 400);
        },

        /**
         * Clear the graph search
         */
        clearGraphSearch() {
            this.graphSearchQuery = '';
            this.graphSearchResult = null;
            
            // Clear any ongoing highlight animation
            if (this.highlightInterval) {
                clearInterval(this.highlightInterval);
                this.highlightInterval = null;
            }
            
            // Deselect any selected nodes
            if (this.network) {
                this.network.unselectAll();
            }
        },

        /**
         * Expand/add a new address to the graph
         */
        async expandAddress() {
            const address = this.expandAddressQuery.trim();
            
            if (!address) {
                alert('Please enter an address');
                return;
            }

            if (address.length < 47) {
                alert('Please enter a valid SS58 address (47-48 characters)');
                return;
            }

            // Expand the node
            await this.expandNode(address);
            
            // Clear the input if successful
            if (this.nodes.get(address)) {
                this.expandAddressQuery = '';
            }
        },

        /**
         * Navigate to another address
         */
        goToAddress() {
            const address = this.addressInput.trim();
            if (address && address.length >= 47 && address.length <= 48) {
                window.location.href = `/connections/${address}`;
            } else {
                alert('Please enter a valid SS58 address (47-48 characters)');
            }
        },

        /**
         * Open save/load modal
         */
        openSaveLoadModal() {
            this.loadSavedGraphsList();
            this.saveLoadModalOpen = true;
            // Pre-fill with current loaded graph name if available
            this.saveGraphName = this.currentLoadedGraphName || '';
        },

        /**
         * Close save/load modal
         */
        closeSaveLoadModal() {
            this.saveLoadModalOpen = false;
            this.saveGraphName = '';
        },

        /**
         * Load list of saved graphs from localStorage
         */
        loadSavedGraphsList() {
            try {
                const saved = localStorage.getItem('savedConnectionGraphs');
                if (saved) {
                    const graphs = JSON.parse(saved);
                    this.savedGraphs = Object.keys(graphs).map(key => ({
                        id: key,
                        name: graphs[key].name,
                        timestamp: graphs[key].timestamp,
                        nodeCount: graphs[key].nodeCount,
                        edgeCount: graphs[key].edgeCount,
                        rootAddress: graphs[key].rootAddress
                    })).sort((a, b) => b.timestamp - a.timestamp);
                } else {
                    this.savedGraphs = [];
                }
            } catch (error) {
                console.error('Error loading saved graphs list:', error);
                this.savedGraphs = [];
            }
        },

        /**
         * Save current graph to localStorage
         */
        saveCurrentGraph() {
            const name = this.saveGraphName.trim();
            
            if (!name) {
                alert('Please enter a name for the graph');
                return;
            }

            if (!this.network || !this.nodes || !this.edges) {
                alert('No graph to save');
                return;
            }

            this.savingGraph = true;

            try {
                // Get existing saved graphs
                const saved = localStorage.getItem('savedConnectionGraphs');
                const graphs = saved ? JSON.parse(saved) : {};

                // Check if name already exists
                const existingGraph = Object.entries(graphs).find(([id, graph]) => graph.name === name);
                
                if (existingGraph) {
                    const [existingId, existingGraphData] = existingGraph;
                    
                    // If it's the currently loaded graph, allow overwrite
                    if (this.currentLoadedGraphId === existingId) {
                        if (!confirm(`Overwrite the loaded graph "${name}"?`)) {
                            this.savingGraph = false;
                            return;
                        }
                        // Will overwrite below
                    } else {
                        // Different graph with same name - not allowed
                        alert(`A graph named "${name}" already exists. Please use a different name.`);
                        this.savingGraph = false;
                        return;
                    }
                }

                // Get all current nodes and edges
                const nodesData = this.nodes.get();
                const edgesData = this.edges.get();

                // Create graph state
                const graphState = {
                    name: name,
                    timestamp: Date.now(),
                    rootAddress: this.currentAddress,
                    nodeCount: nodesData.length,
                    edgeCount: edgesData.length,
                    nodes: nodesData,
                    edges: edgesData,
                    expandedAddresses: Array.from(this.expandedAddresses),
                    stats: { ...this.stats }
                };

                // Determine ID: use existing if overwriting, otherwise create new
                let id;
                if (this.currentLoadedGraphId && existingGraph && existingGraph[0] === this.currentLoadedGraphId) {
                    // Overwriting loaded graph
                    id = this.currentLoadedGraphId;
                } else {
                    // New graph
                    id = `graph_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
                    this.currentLoadedGraphId = id;
                    this.currentLoadedGraphName = name;
                }

                graphs[id] = graphState;

                // Save to localStorage
                localStorage.setItem('savedConnectionGraphs', JSON.stringify(graphs));

                alert(`Graph "${name}" saved successfully!`);
                this.saveGraphName = '';
                this.loadSavedGraphsList();
            } catch (error) {
                console.error('Error saving graph:', error);
                alert('Failed to save graph. It might be too large for localStorage.');
            } finally {
                this.savingGraph = false;
            }
        },

        /**
         * Load a saved graph
         */
        async loadSavedGraph(graphId, skipConfirm = false) {
            try {
                const saved = localStorage.getItem('savedConnectionGraphs');
                if (!saved) {
                    if (!skipConfirm) alert('No saved graphs found');
                    return;
                }

                const graphs = JSON.parse(saved);
                const graphState = graphs[graphId];

                if (!graphState) {
                    if (!skipConfirm) alert('Graph not found');
                    return;
                }

                // Confirm loading (skip if loading from home page)
                if (!skipConfirm && !confirm(`Load graph "${graphState.name}"?\n\nThis will replace the current graph.`)) {
                    return;
                }

                this.loading = true;
                if (this.saveLoadModalOpen) {
                    this.closeSaveLoadModal();
                }

                // Restore state
                this.currentAddress = graphState.rootAddress;
                this.expandedAddresses = new Set(graphState.expandedAddresses || []);
                this.stats = { ...graphState.stats };
                this.hasData = true;

                // Wait for DOM update
                await this.$nextTick();

                // Recreate the network with saved data
                const container = document.getElementById('network-container');
                if (!container) {
                    console.error('Network container not found');
                    return;
                }

                // Update nodes with current labels (don't use saved labels)
                const updatedNodes = graphState.nodes.map(node => {
                    const hasLabel = !!addressLabels.getLabel(node.id);
                    return {
                        ...node,
                        label: this.formatNodeLabel(node.id), // Use current label
                        font: {
                            ...node.font,
                            color: hasLabel ? '#10b981' : '#ffffff' // Update font color based on current label
                        }
                    };
                });

                // Create vis-network dataset from saved data
                const nodes = new vis.DataSet(updatedNodes);
                const edges = new vis.DataSet(graphState.edges);

                // Network options (same as buildNetworkGraph)
                const options = {
                    nodes: {
                        borderWidth: 1,
                        borderWidthSelected: 3,
                        shadow: {
                            enabled: true,
                            color: 'rgba(0,0,0,0.5)',
                            size: 6,
                            x: 0,
                            y: 0
                        }
                    },
                    edges: {
                        smooth: {
                            forceDirection: "none",
                            roundness: 0.6
                        },
                        arrows: {
                            to: {
                                enabled: true,
                                scaleFactor: 0.3
                            },
                            from: {
                                enabled: true,
                                scaleFactor: 0.3
                            }
                        }
                    },
                    physics: {
                        forceAtlas2Based: {
                            springLength: 100
                        },
                        maxVelocity: 8,
                        minVelocity: 0.34,
                        solver: "forceAtlas2Based"
                    },
                    interaction: {
                        hover: true,
                        tooltipDelay: 200,
                        zoomView: true,
                        dragView: true
                    }
                };

                // Destroy existing network
                if (this.network) {
                    this.network.destroy();
                }

                // Create new network
                this.network = new vis.Network(container, { nodes, edges }, options);

                // Add click event
                this.network.on('click', (params) => {
                    if (params.nodes.length > 0) {
                        const nodeId = params.nodes[0];
                        this.showNodePopup(nodeId, params.pointer.DOM);
                    } else if (params.edges.length > 0) {
                        const edgeId = params.edges[0];
                        this.handleEdgeClick(edgeId);
                        this.selectedNode = null;
                    } else {
                        this.selectedNode = null;
                    }
                });

                // Store references
                this.nodes = nodes;
                this.edges = edges;

                // Update page title
                const addressPrefix = this.currentAddress.substring(0, 5);
                document.title = `${addressPrefix} | AlphaStats`;

                // Update URL without reloading
                window.history.pushState({}, '', `/connections/${this.currentAddress}`);

                // Track the loaded graph
                this.currentLoadedGraphId = graphId;
                this.currentLoadedGraphName = graphState.name;

                if (!skipConfirm) {
                    alert(`Graph "${graphState.name}" loaded successfully!`);
                }
            } catch (error) {
                console.error('Error loading graph:', error);
                if (!skipConfirm) {
                    alert('Failed to load graph. The data might be corrupted.');
                }
            } finally {
                this.loading = false;
            }
        },

        /**
         * Delete a saved graph
         */
        deleteSavedGraph(graphId, graphName) {
            if (!confirm(`Delete graph "${graphName}"?\n\nThis action cannot be undone.`)) {
                return;
            }

            try {
                const saved = localStorage.getItem('savedConnectionGraphs');
                if (!saved) {
                    return;
                }

                const graphs = JSON.parse(saved);
                delete graphs[graphId];

                localStorage.setItem('savedConnectionGraphs', JSON.stringify(graphs));
                
                // If deleting the currently loaded graph, clear tracking
                if (this.currentLoadedGraphId === graphId) {
                    this.currentLoadedGraphId = null;
                    this.currentLoadedGraphName = null;
                }
                
                this.loadSavedGraphsList();
                alert(`Graph "${graphName}" deleted successfully!`);
            } catch (error) {
                console.error('Error deleting graph:', error);
                alert('Failed to delete graph');
            }
        },

        /**
         * Export graph as JSON file
         */
        exportGraph(graphId) {
            try {
                const saved = localStorage.getItem('savedConnectionGraphs');
                if (!saved) {
                    alert('No saved graphs found');
                    return;
                }

                const graphs = JSON.parse(saved);
                const graphState = graphs[graphId];

                if (!graphState) {
                    alert('Graph not found');
                    return;
                }

                // Create JSON file
                const jsonStr = JSON.stringify(graphState, null, 2);
                const blob = new Blob([jsonStr], { type: 'application/json' });
                const url = URL.createObjectURL(blob);

                // Create download link
                const a = document.createElement('a');
                a.href = url;
                a.download = `${graphState.name.replace(/[^a-z0-9]/gi, '_')}_${Date.now()}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            } catch (error) {
                console.error('Error exporting graph:', error);
                alert('Failed to export graph');
            }
        },

        /**
         * Format timestamp for display
         */
        formatGraphTimestamp(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleString();
        }
    };
}

