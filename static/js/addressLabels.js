/**
 * Address Labels Management
 * LocalStorage-based address labeling system
 */

const ADDRESS_LABELS_KEY = 'bittensor_address_labels';
const DEFAULT_ADDRESS_LABELS_KEY = 'bittensor_default_address_labels';
const ADDRESS_TYPES_KEY = 'bittensor_address_types';
const DEFAULT_ADDRESS_TYPES_KEY = 'bittensor_default_address_types';
const ADDRESS_VERSION_KEY = 'bittensor_address_labels_version';
const CURRENT_VERSION = 'v2';

/**
 * Address Labels Storage Manager
 */
class AddressLabelsManager {
    constructor() {
        this.storageKey = ADDRESS_LABELS_KEY;
        this.defaultStorageKey = DEFAULT_ADDRESS_LABELS_KEY;
        this.typesKey = ADDRESS_TYPES_KEY;
        this.defaultTypesKey = DEFAULT_ADDRESS_TYPES_KEY;
        this.versionKey = ADDRESS_VERSION_KEY;
        this.currentVersion = CURRENT_VERSION;
        
        // Check version and migrate if needed
        this.checkAndMigrate();
        
        this.labels = this.loadLabels(); // {address: [label, type]}
        this.defaultLabels = this.loadDefaultLabels(); // {address: [label, type]}
        this.types = this.loadTypes(); // {type_name: color}
        this.defaultTypes = this.loadDefaultTypes(); // {type_name: color}
    }

    /**
     * Check version and migrate if needed
     */
    checkAndMigrate() {
        try {
            const storedVersion = localStorage.getItem(this.versionKey);
            
            // If no version is stored, we need to migrate from v1 to v2
            if (!storedVersion) {
                console.log('📦 Migrating address labels from v1 to v2...');
                this.migrateV1ToV2();
                localStorage.setItem(this.versionKey, this.currentVersion);
                console.log('✅ Migration complete! Now using version', this.currentVersion);
            } else if (storedVersion !== this.currentVersion) {
                console.log(`📦 Address labels version mismatch. Stored: ${storedVersion}, Current: ${this.currentVersion}`);
                // Future migrations can be added here
            }
        } catch (error) {
            console.error('Error checking address labels version:', error);
        }
    }

    /**
     * Migrate from v1 format to v2 format
     * v1: {address: label_string}
     * v2: {address: [label, type]}
     */
    migrateV1ToV2() {
        try {
            // Migrate user labels
            const userLabelsData = localStorage.getItem(this.storageKey);
            if (userLabelsData) {
                const parsed = JSON.parse(userLabelsData);
                let migrated = false;
                for (const [address, value] of Object.entries(parsed)) {
                    if (typeof value === 'string') {
                        parsed[address] = [value, ''];
                        migrated = true;
                    }
                }
                if (migrated) {
                    localStorage.setItem(this.storageKey, JSON.stringify(parsed));
                    console.log('  ✓ Migrated user labels');
                }
            }

            // Migrate default labels
            const defaultLabelsData = localStorage.getItem(this.defaultStorageKey);
            if (defaultLabelsData) {
                const parsed = JSON.parse(defaultLabelsData);
                let migrated = false;
                for (const [address, value] of Object.entries(parsed)) {
                    if (typeof value === 'string') {
                        parsed[address] = [value, ''];
                        migrated = true;
                    }
                }
                if (migrated) {
                    localStorage.setItem(this.defaultStorageKey, JSON.stringify(parsed));
                    console.log('  ✓ Migrated default labels');
                }
            }
        } catch (error) {
            console.error('Error during v1 to v2 migration:', error);
        }
    }

    /**
     * Load all labels from localStorage
     * @returns {Object} Object mapping addresses to [label, type]
     */
    loadLabels() {
        try {
            const data = localStorage.getItem(this.storageKey);
            return data ? JSON.parse(data) : {};
        } catch (error) {
            console.error('Error loading address labels:', error);
            return {};
        }
    }

    /**
     * Load default labels from localStorage
     * @returns {Object} Object mapping addresses to [label, type]
     */
    loadDefaultLabels() {
        try {
            const data = localStorage.getItem(this.defaultStorageKey);
            return data ? JSON.parse(data) : {};
        } catch (error) {
            console.error('Error loading default address labels:', error);
            return {};
        }
    }

    /**
     * Load user types from localStorage
     * @returns {Object} Object mapping type names to colors
     */
    loadTypes() {
        try {
            const data = localStorage.getItem(this.typesKey);
            return data ? JSON.parse(data) : {};
        } catch (error) {
            console.error('Error loading address types:', error);
            return {};
        }
    }

    /**
     * Load default types from localStorage
     * @returns {Object} Object mapping type names to colors
     */
    loadDefaultTypes() {
        try {
            const data = localStorage.getItem(this.defaultTypesKey);
            return data ? JSON.parse(data) : {};
        } catch (error) {
            console.error('Error loading default address types:', error);
            return {};
        }
    }

    /**
     * Save default labels to localStorage
     */
    saveDefaultLabels() {
        try {
            localStorage.setItem(this.defaultStorageKey, JSON.stringify(this.defaultLabels));
            return true;
        } catch (error) {
            console.error('Error saving default address labels:', error);
            return false;
        }
    }

    /**
     * Save labels to localStorage
     */
    saveLabels() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.labels));
            return true;
        } catch (error) {
            console.error('Error saving address labels:', error);
            return false;
        }
    }

    /**
     * Save user types to localStorage
     */
    saveTypes() {
        try {
            localStorage.setItem(this.typesKey, JSON.stringify(this.types));
            return true;
        } catch (error) {
            console.error('Error saving address types:', error);
            return false;
        }
    }

    /**
     * Save default types to localStorage
     */
    saveDefaultTypes() {
        try {
            localStorage.setItem(this.defaultTypesKey, JSON.stringify(this.defaultTypes));
            return true;
        } catch (error) {
            console.error('Error saving default address types:', error);
            return false;
        }
    }

    /**
     * Get label for an address (prioritizes user labels over defaults)
     * @param {string} address - The blockchain address
     * @returns {string|null} The label or null if not found
     */
    getLabel(address) {
        if (!address) return null;
        // User labels take priority over default labels
        const labelData = this.labels[address] || this.defaultLabels[address];
        if (!labelData) return null;
        // Return just the label part (first element)
        return Array.isArray(labelData) ? labelData[0] : labelData;
    }

    /**
     * Get label and type for an address
     * @param {string} address - The blockchain address
     * @returns {[string, string]|null} [label, type] or null if not found
     */
    getLabelWithType(address) {
        if (!address) return null;
        const labelData = this.labels[address] || this.defaultLabels[address];
        if (!labelData) return null;
        // Ensure it's in [label, type] format
        if (Array.isArray(labelData)) {
            return labelData.length === 2 ? labelData : [labelData[0], ''];
        }
        return [labelData, ''];
    }

    /**
     * Get type color for an address (uses type from label data)
     * @param {string} address - The blockchain address
     * @returns {string|null} The color hex code or null if not found
     */
    getTypeColor(address) {
        const labelData = this.getLabelWithType(address);
        if (!labelData || !labelData[1]) return null;
        const typeName = labelData[1];
        // User types take priority over default types
        return this.types[typeName] || this.defaultTypes[typeName] || null;
    }

    /**
     * Check if an address has a default label
     * @param {string} address - The blockchain address
     * @returns {boolean} True if address has a default label
     */
    isDefaultLabel(address) {
        if (!address) return false;
        return !this.labels[address] && !!this.defaultLabels[address];
    }

    /**
     * Set label for an address
     * @param {string} address - The blockchain address
     * @param {string} label - The label to set
     * @param {string} type - The type to set (optional, defaults to empty string)
     * @returns {boolean} Success status
     */
    setLabel(address, label, type = '') {
        if (!address || !label) return false;
        
        const trimmedLabel = label.trim();
        if (!trimmedLabel) return false;

        this.labels[address] = [trimmedLabel, type || ''];
        return this.saveLabels();
    }

    /**
     * Set type for an address (keeps existing label)
     * @param {string} address - The blockchain address
     * @param {string} type - The type to set
     * @returns {boolean} Success status
     */
    setType(address, type) {
        if (!address) return false;
        
        const labelData = this.labels[address];
        if (!labelData) return false;
        
        const label = Array.isArray(labelData) ? labelData[0] : labelData;
        this.labels[address] = [label, type || ''];
        return this.saveLabels();
    }

    /**
     * Remove label for an address
     * @param {string} address - The blockchain address
     * @returns {boolean} Success status
     */
    removeLabel(address) {
        if (!address) return false;
        
        delete this.labels[address];
        return this.saveLabels();
    }

    /**
     * Get all labeled addresses (includes both user and default labels)
     * @returns {Array} Array of {address, label, type, isDefault} objects
     */
    getAllLabels() {
        const userLabels = Object.entries(this.labels).map(([address, labelData]) => {
            const [label, type] = Array.isArray(labelData) ? labelData : [labelData, ''];
            return {
                address,
                label,
                type: type || '',
                isDefault: false
            };
        });

        // Add default labels that aren't overridden by user labels
        const defaultLabels = Object.entries(this.defaultLabels)
            .filter(([address]) => !this.labels[address])
            .map(([address, labelData]) => {
                const [label, type] = Array.isArray(labelData) ? labelData : [labelData, ''];
                return {
                    address,
                    label,
                    type: type || '',
                    isDefault: true
                };
            });

        return [...userLabels, ...defaultLabels];
    }

    /**
     * Get only user-created labels
     * @returns {Array} Array of {address, label, type} objects
     */
    getUserLabels() {
        return Object.entries(this.labels).map(([address, labelData]) => {
            const [label, type] = Array.isArray(labelData) ? labelData : [labelData, ''];
            return {
                address,
                label,
                type: type || '',
                isDefault: false
            };
        });
    }

    /**
     * Get only default labels (not overridden by user)
     * @returns {Array} Array of {address, label, type} objects
     */
    getDefaultLabels() {
        return Object.entries(this.defaultLabels)
            .filter(([address]) => !this.labels[address])
            .map(([address, labelData]) => {
                const [label, type] = Array.isArray(labelData) ? labelData : [labelData, ''];
                return {
                    address,
                    label,
                    type: type || '',
                    isDefault: true
                };
            });
    }

    /**
     * Check if an address has a label
     * @param {string} address - The blockchain address
     * @returns {boolean} True if address has a label
     */
    hasLabel(address) {
        return !!this.labels[address];
    }

    /**
     * Get count of labeled addresses
     * @returns {number} Number of labeled addresses
     */
    getCount() {
        return Object.keys(this.labels).length;
    }

    /**
     * Clear all labels (use with caution)
     * @returns {boolean} Success status
     */
    clearAll() {
        this.labels = {};
        return this.saveLabels();
    }

    /**
     * Export labels as JSON string
     * @returns {string} JSON string of all labels
     */
    exportLabels() {
        return JSON.stringify(this.labels, null, 2);
    }

    /**
     * Import labels from JSON string
     * @param {string} jsonString - JSON string to import
     * @param {boolean} merge - If true, merge with existing labels; if false, replace
     * @returns {boolean} Success status
     */
    importLabels(jsonString, merge = false) {
        try {
            const imported = JSON.parse(jsonString);
            
            if (typeof imported !== 'object' || imported === null) {
                throw new Error('Invalid format');
            }

            if (merge) {
                this.labels = { ...this.labels, ...imported };
            } else {
                this.labels = imported;
            }

            return this.saveLabels();
        } catch (error) {
            console.error('Error importing labels:', error);
            return false;
        }
    }

    /**
     * Search labels by address or label text
     * @param {string} query - Search query
     * @returns {Array} Array of matching {address, label, isDefault} objects
     */
    search(query) {
        if (!query) return this.getAllLabels();
        
        const lowerQuery = query.toLowerCase();
        return this.getAllLabels().filter(item => 
            item.address.toLowerCase().includes(lowerQuery) ||
            item.label.toLowerCase().includes(lowerQuery)
        );
    }

    /**
     * Fetch and update default addresses from the API
     * @returns {Promise<{success: boolean, count: number, error?: string}>}
     */
    async updateDefaultAddresses() {
        try {
            const response = await authFetch('/api/v1/address/default');
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (!data || typeof data !== 'object') {
                throw new Error('Invalid response format');
            }

            // New API format: {addresses: {address: [label, type]}, types: {type: color}}
            this.defaultLabels = data.addresses || {};
            this.defaultTypes = data.types || {};
            
            const saved = this.saveDefaultLabels() && this.saveDefaultTypes();
            
            if (saved) {
                return {
                    success: true,
                    count: Object.keys(this.defaultLabels).length
                };
            } else {
                return {
                    success: false,
                    count: 0,
                    error: 'Failed to save to localStorage'
                };
            }
        } catch (error) {
            console.error('Error fetching default addresses:', error);
            return {
                success: false,
                count: 0,
                error: error.message
            };
        }
    }

    /**
     * Get all types (user and default combined)
     * @returns {Object} Object mapping type names to colors
     */
    getAllTypes() {
        return { ...this.defaultTypes, ...this.types };
    }

    /**
     * Get a type's color
     * @param {string} typeName - The type name
     * @returns {string|null} The color or null if not found
     */
    getType(typeName) {
        return this.types[typeName] || this.defaultTypes[typeName] || null;
    }

    /**
     * Add or update a type
     * @param {string} typeName - The type name
     * @param {string} color - The color hex code
     * @returns {boolean} Success status
     */
    addType(typeName, color) {
        if (!typeName || !color) return false;
        this.types[typeName] = color;
        return this.saveTypes();
    }

    /**
     * Remove a user type
     * @param {string} typeName - The type name
     * @returns {boolean} Success status
     */
    removeType(typeName) {
        if (!typeName) return false;
        delete this.types[typeName];
        return this.saveTypes();
    }

    /**
     * Check if a type is a default type (cannot be edited/removed)
     * @param {string} typeName - The type name
     * @returns {boolean} True if it's a default type
     */
    isDefaultType(typeName) {
        return !!this.defaultTypes[typeName] && !this.types[typeName];
    }

    /**
     * Get current version of address labels storage
     * @returns {string} Current version (e.g., 'v2')
     */
    getVersion() {
        return localStorage.getItem(this.versionKey) || 'v1 (not migrated)';
    }

    /**
     * Get count of default labels
     * @returns {number} Number of default labeled addresses
     */
    getDefaultCount() {
        return Object.keys(this.defaultLabels).length;
    }
}

// Create global instance
const addressLabels = new AddressLabelsManager();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AddressLabelsManager, addressLabels };
}

