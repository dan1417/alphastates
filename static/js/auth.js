/**
 * Authentication helper functions for client-side authentication
 */

// Check if user is authenticated
function checkAuth() {
    const token = localStorage.getItem('auth_token');
    
    if (!token) {
        // No token, redirect to login
        window.location.href = '/';
        return null;
    }
    
    return token;
}

// Verify token with backend
async function verifyAuth() {
    const token = checkAuth();
    
    if (!token) {
        return false;
    }
    
    try {
        const response = await fetch('/api/v1/auth/me', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (!response.ok) {
            // Token is invalid, remove it and redirect
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return false;
        }
        
        return true;
    } catch (error) {
        console.error('Auth verification failed:', error);
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return false;
    }
}

// Get auth headers for API requests
function getAuthHeaders() {
    const token = localStorage.getItem('auth_token');
    
    return {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
    };
}

// Logout function
function logout() {
    localStorage.removeItem('auth_token');
    window.location.href = '/';
}

// Make authenticated fetch request
async function authFetch(url, options = {}) {
    const token = localStorage.getItem('auth_token');
    
    if (!token) {
        window.location.href = '/';
        throw new Error('Not authenticated');
    }
    
    // Merge auth headers with provided options
    const authOptions = {
        ...options,
        headers: {
            ...options.headers,
            'Authorization': `Bearer ${token}`
        }
    };
    
    try {
        const response = await fetch(url, authOptions);
        
        // If unauthorized, redirect to login
        if (response.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            throw new Error('Authentication expired');
        }
        
        return response;
    } catch (error) {
        // Network error or auth error
        if (error.message === 'Authentication expired' || error.message === 'Not authenticated') {
            throw error;
        }
        // Re-throw other errors
        throw error;
    }
}

// Initialize auth check on page load
document.addEventListener('DOMContentLoaded', function() {
    // Only check auth on protected pages (not on auth.html itself)
    if (!window.location.pathname.includes('auth.html') && 
        !window.location.pathname.includes('/api/')) {
        verifyAuth();
    }
});

