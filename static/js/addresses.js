/**
 * Address Management App
 * Alpine.js component for managing address labels
 */

function addressManagementApp() {
    return {
        // State
        labels: [],
        filteredLabels: [],
        searchQuery: '',
        newAddress: '',
        newLabel: '',
        newType: '',
        editingAddress: null,
        editLabel: '',
        editType: '',
        message: '',
        messageType: '',
        showImportExport: false,
        importMerge: false,
        isUpdatingDefaults: false,
        activeTab: 'all', // 'all', 'user', 'default'
        
        // Type management
        types: {},
        showTypeManagement: false,
        newTypeName: '',
        newTypeColor: '#3b82f6',
        editingType: null,
        editTypeName: '',
        editTypeColor: '',

        // Initialize
        init() {
            this.loadLabels();
            this.loadTypes();
        },

        /**
         * Load all labels from storage
         */
        loadLabels() {
            this.labels = addressLabels.getAllLabels();
            this.filteredLabels = [...this.labels];
            this.sortLabels();
        },

        /**
         * Load all types from storage
         */
        loadTypes() {
            this.types = addressLabels.getAllTypes();
        },

        /**
         * Sort labels alphabetically by label name
         */
        sortLabels() {
            this.labels.sort((a, b) => a.label.localeCompare(b.label));
            this.filteredLabels.sort((a, b) => a.label.localeCompare(b.label));
        },

        /**
         * Add a new label
         */
        addLabel() {
            const address = this.newAddress.trim();
            const label = this.newLabel.trim();
            const type = this.newType || '';

            if (!address || !label) {
                this.showMessage('Please enter both address and label', 'error');
                return;
            }

            // Basic validation - check if address looks reasonable
            if (address.length < 10) {
                this.showMessage('Address seems too short. Please enter a valid address.', 'error');
                return;
            }

            const success = addressLabels.setLabel(address, label, type);
            
            if (success) {
                this.showMessage('Label added successfully!', 'success');
                this.newAddress = '';
                this.newLabel = '';
                this.newType = '';
                this.loadLabels();
                this.performSearch(); // Refresh filtered results
            } else {
                this.showMessage('Failed to add label. Please try again.', 'error');
            }
        },

        /**
         * Remove a label
         */
        removeLabel(address) {
            // Check if this is a default label
            const item = this.labels.find(l => l.address === address);
            if (item && item.isDefault) {
                this.showMessage('Cannot remove default labels. These are read-only.', 'error');
                return;
            }

            if (!confirm(`Remove label for ${address}?`)) {
                return;
            }

            const success = addressLabels.removeLabel(address);
            
            if (success) {
                this.showMessage('Label removed successfully!', 'success');
                this.loadLabels();
                this.performSearch(); // Refresh filtered results
            } else {
                this.showMessage('Failed to remove label. Please try again.', 'error');
            }
        },

        /**
         * Start editing a label
         */
        startEdit(item) {
            // Prevent editing default labels
            if (item.isDefault) {
                this.showMessage('Cannot edit default labels. These are read-only.', 'error');
                return;
            }

            this.editingAddress = item.address;
            this.editLabel = item.label;
            this.editType = item.type || '';
            
            // Focus the input after Alpine renders it
            this.$nextTick(() => {
                const input = document.querySelector('.edit-form input');
                if (input) input.focus();
            });
        },

        /**
         * Save edited label
         */
        saveEdit(address) {
            const newLabel = this.editLabel.trim();
            const newType = this.editType || '';
            
            if (!newLabel) {
                this.showMessage('Label cannot be empty', 'error');
                return;
            }

            const success = addressLabels.setLabel(address, newLabel, newType);
            
            if (success) {
                this.showMessage('Label updated successfully!', 'success');
                this.editingAddress = null;
                this.editLabel = '';
                this.editType = '';
                this.loadLabels();
                this.performSearch(); // Refresh filtered results
            } else {
                this.showMessage('Failed to update label. Please try again.', 'error');
            }
        },

        /**
         * Cancel editing
         */
        cancelEdit() {
            this.editingAddress = null;
            this.editLabel = '';
        },

        /**
         * Switch tab
         */
        switchTab(tab) {
            this.activeTab = tab;
            this.performSearch(); // Refilter based on new tab
        },

        /**
         * Get labels for current tab
         */
        getLabelsForTab() {
            switch (this.activeTab) {
                case 'user':
                    return this.labels.filter(item => !item.isDefault);
                case 'default':
                    return this.labels.filter(item => item.isDefault);
                case 'all':
                default:
                    return this.labels;
            }
        },

        /**
         * Get count for a specific tab
         */
        getTabCount(tab) {
            switch (tab) {
                case 'user':
                    return this.labels.filter(item => !item.isDefault).length;
                case 'default':
                    return this.labels.filter(item => item.isDefault).length;
                case 'all':
                default:
                    return this.labels.length;
            }
        },

        /**
         * Perform search
         */
        performSearch() {
            let baseLabels = this.getLabelsForTab();
            
            if (!this.searchQuery) {
                this.filteredLabels = [...baseLabels];
            } else {
                const lowerQuery = this.searchQuery.toLowerCase();
                this.filteredLabels = baseLabels.filter(item => 
                    item.address.toLowerCase().includes(lowerQuery) ||
                    item.label.toLowerCase().includes(lowerQuery)
                );
            }
            this.sortLabels();
        },

        /**
         * Clear search
         */
        clearSearch() {
            this.searchQuery = '';
            this.performSearch();
        },

        /**
         * Copy address to clipboard
         */
        async copyAddress(address) {
            try {
                await navigator.clipboard.writeText(address);
                this.showMessage('Address copied to clipboard!', 'success');
            } catch (error) {
                console.error('Failed to copy address:', error);
                this.showMessage('Failed to copy address', 'error');
            }
        },

        /**
         * Export labels to JSON file
         */
        exportLabels() {
            const data = addressLabels.exportLabels();
            const blob = new Blob([data], { type: 'application/json' });
            const url = URL.createObjectURL(blob);

            const a = document.createElement('a');
            a.href = url;
            a.download = `address-labels-${Date.now()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            this.showMessage('Labels exported successfully!', 'success');
        },

        /**
         * Export labels to the clipboard as JSON
         */
        async exportLabelsToClipboard() {
            const data = addressLabels.exportLabels();
            try {
                await navigator.clipboard.writeText(data);
                this.showMessage('Labels copied to clipboard!', 'success');
            } catch (error) {
                console.error('Failed to copy labels:', error);
                this.showMessage('Failed to copy labels to clipboard', 'error');
            }
        },

        /**
         * Import labels from JSON file
         */
        async importLabels(event) {
            const file = event.target.files[0];
            if (!file) return;

            try {
                const text = await file.text();
                const success = addressLabels.importLabels(text, this.importMerge);
                
                if (success) {
                    this.showMessage(
                        `Labels ${this.importMerge ? 'merged' : 'imported'} successfully!`,
                        'success'
                    );
                    this.loadLabels();
                    this.performSearch(); // Refresh filtered results
                } else {
                    this.showMessage('Failed to import labels. Invalid file format.', 'error');
                }
            } catch (error) {
                console.error('Import error:', error);
                this.showMessage('Failed to import labels. Please check the file.', 'error');
            }

            // Reset file input
            event.target.value = '';
        },

        /**
         * Confirm and clear all user labels
         */
        confirmClearAll() {
            const userLabels = this.labels.filter(item => !item.isDefault);
            const count = userLabels.length;
            
            if (count === 0) {
                this.showMessage('No user labels to clear.', 'info');
                return;
            }
            
            if (!confirm(`Are you sure you want to remove all ${count} user label(s)? This action cannot be undone. Default labels will not be affected.`)) {
                return;
            }

            const success = addressLabels.clearAll();
            
            if (success) {
                this.showMessage('All user labels cleared successfully!', 'success');
                this.loadLabels();
                this.performSearch(); // Refresh filtered results
            } else {
                this.showMessage('Failed to clear labels. Please try again.', 'error');
            }
        },

        /**
         * Update default addresses from the API
         */
        async updateDefaultAddresses() {
            if (this.isUpdatingDefaults) {
                return; // Prevent multiple simultaneous updates
            }

            this.isUpdatingDefaults = true;
            this.showMessage('Fetching default addresses...', 'info');

            try {
                const result = await addressLabels.updateDefaultAddresses();
                
                if (result.success) {
                    this.showMessage(
                        `Successfully updated ${result.count} default address label(s)!`,
                        'success'
                    );
                    this.loadLabels();
                    this.loadTypes(); // Also reload types
                    this.performSearch(); // Refresh filtered results
                } else {
                    this.showMessage(
                        `Failed to update default addresses: ${result.error || 'Unknown error'}`,
                        'error'
                    );
                }
            } catch (error) {
                console.error('Error updating default addresses:', error);
                this.showMessage('Failed to update default addresses. Please try again.', 'error');
            } finally {
                this.isUpdatingDefaults = false;
            }
        },

        /**
         * Add a new type
         */
        addNewType() {
            const typeName = this.newTypeName.trim();
            const color = this.newTypeColor;

            if (!typeName) {
                this.showMessage('Please enter a type name', 'error');
                return;
            }

            // Check if it's a default type
            if (addressLabels.isDefaultType(typeName)) {
                this.showMessage('Cannot override default types', 'error');
                return;
            }

            const success = addressLabels.addType(typeName, color);
            
            if (success) {
                this.showMessage('Type added successfully!', 'success');
                this.newTypeName = '';
                this.newTypeColor = '#3b82f6';
                this.loadTypes();
            } else {
                this.showMessage('Failed to add type. Please try again.', 'error');
            }
        },

        /**
         * Start editing a type
         */
        startEditType(typeName) {
            // Prevent editing default types
            if (addressLabels.isDefaultType(typeName)) {
                this.showMessage('Cannot edit default types. These are read-only.', 'error');
                return;
            }

            this.editingType = typeName;
            this.editTypeName = typeName;
            this.editTypeColor = this.types[typeName] || '#3b82f6';
        },

        /**
         * Save edited type
         */
        saveEditType(oldTypeName) {
            const newTypeName = this.editTypeName.trim();
            const newColor = this.editTypeColor;
            
            if (!newTypeName) {
                this.showMessage('Type name cannot be empty', 'error');
                return;
            }

            // If name changed, remove old and add new
            if (newTypeName !== oldTypeName) {
                addressLabels.removeType(oldTypeName);
            }
            
            const success = addressLabels.addType(newTypeName, newColor);
            
            if (success) {
                this.showMessage('Type updated successfully!', 'success');
                this.editingType = null;
                this.editTypeName = '';
                this.editTypeColor = '';
                this.loadTypes();
            } else {
                this.showMessage('Failed to update type. Please try again.', 'error');
            }
        },

        /**
         * Cancel editing type
         */
        cancelEditType() {
            this.editingType = null;
            this.editTypeName = '';
            this.editTypeColor = '';
        },

        /**
         * Remove a type
         */
        removeType(typeName) {
            // Check if this is a default type
            if (addressLabels.isDefaultType(typeName)) {
                this.showMessage('Cannot remove default types. These are read-only.', 'error');
                return;
            }

            if (!confirm(`Remove type "${typeName}"? Labels using this type will still keep the type name but won't have a color.`)) {
                return;
            }

            const success = addressLabels.removeType(typeName);
            
            if (success) {
                this.showMessage('Type removed successfully!', 'success');
                this.loadTypes();
            } else {
                this.showMessage('Failed to remove type. Please try again.', 'error');
            }
        },

        /**
         * Get color for a type (returns empty string if not found)
         */
        getTypeColor(typeName) {
            return this.types[typeName] || '';
        },

        /**
         * Check if a type is a default type
         */
        isDefaultType(typeName) {
            return addressLabels.isDefaultType(typeName);
        },

        /**
         * Show a message to the user
         */
        showMessage(text, type = 'info') {
            this.message = text;
            this.messageType = type;
            
            // Auto-hide after 3 seconds
            setTimeout(() => {
                this.message = '';
            }, 3000);
        }
    };
}

