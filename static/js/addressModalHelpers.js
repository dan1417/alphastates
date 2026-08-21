/**
 * Shared Address Modal Helper Functions
 * These functions can be mixed into any Alpine.js component that needs address modal functionality
 */

const addressModalHelpers = {
    // Address Modal state
    addressModalOpen: false,
    selectedAddress: null,
    editingLabel: false,
    labelInput: '',
    typeInput: '',
    modalLabelCache: null,
    modalTypeCache: null,
    modalTypes: {},

    /**
     * Open address modal
     */
    openAddressModal(address) {
        this.selectedAddress = address;
        const labelData = addressLabels.getLabelWithType(address);
        this.modalLabelCache = labelData ? labelData[0] : null;
        this.modalTypeCache = labelData ? labelData[1] : '';
        this.modalTypes = addressLabels.getAllTypes();
        this.addressModalOpen = true;
        this.editingLabel = false;
        this.labelInput = '';
        this.typeInput = '';
    },

    /**
     * Close address modal
     */
    closeAddressModal() {
        this.addressModalOpen = false;
        this.selectedAddress = null;
        this.editingLabel = false;
        this.labelInput = '';
        this.typeInput = '';
        this.modalLabelCache = null;
        this.modalTypeCache = null;
    },

    /**
     * Get address label
     */
    getAddressLabel(address) {
        return addressLabels.getLabel(address);
    },

    /**
     * Start editing label
     */
    startEditingLabel() {
        this.labelInput = this.modalLabelCache || '';
        this.typeInput = this.modalTypeCache || '';
        this.editingLabel = true;
        
        // Focus input after Alpine renders it
        this.$nextTick(() => {
            this.$refs.labelInput?.focus();
        });
    },

    /**
     * Cancel editing label
     */
    cancelEditingLabel() {
        this.editingLabel = false;
        this.labelInput = '';
        this.typeInput = '';
    },

    /**
     * Save label from modal
     */
    saveLabelFromModal() {
        const label = this.labelInput.trim();
        const type = this.typeInput || '';
        
        if (!label) {
            alert('Label cannot be empty');
            return;
        }

        if (addressLabels.setLabel(this.selectedAddress, label, type)) {
            this.editingLabel = false;
            this.labelInput = '';
            this.typeInput = '';
            
            // Update cache to trigger modal reactivity
            this.modalLabelCache = label;
            this.modalTypeCache = type;
            
            // Force re-render to show updated labels
            this.$nextTick(() => {
                this.refreshDataForLabelUpdate?.();
            });
        } else {
            alert('Failed to save label. Please try again.');
        }
    },

    /**
     * Remove label from modal
     */
    removeLabelFromModal() {
        if (!confirm('Remove this label?')) {
            return;
        }

        if (addressLabels.removeLabel(this.selectedAddress)) {
            // Update cache to trigger modal reactivity
            this.modalLabelCache = null;
            
            // Force re-render to show updated labels
            this.$nextTick(() => {
                this.refreshDataForLabelUpdate?.();
            });
        } else {
            alert('Failed to remove label. Please try again.');
        }
    },

    /**
     * Copy address from modal
     */
    copyAddressFromModal() {
        navigator.clipboard.writeText(this.selectedAddress).then(() => {
            console.log('Address copied to clipboard');
        }).catch(err => {
            console.error('Failed to copy:', err);
            alert('Failed to copy address');
        });
    },

    /**
     * Get type color for display
     */
    getModalTypeColor(typeName) {
        if (!typeName) return null;
        return this.modalTypes[typeName] || null;
    }
};

