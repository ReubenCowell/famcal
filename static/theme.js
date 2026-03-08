(function() {
    'use strict';
    var STORAGE_KEY = 'famcal-theme';

    function getSystemTheme() {
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyTheme(pref) {
        var theme = pref === 'auto' ? getSystemTheme() : pref;
        document.documentElement.setAttribute('data-theme', theme);
    }

    function getPreference() {
        return localStorage.getItem(STORAGE_KEY) || 'auto';
    }

    function setPreference(pref) {
        localStorage.setItem(STORAGE_KEY, pref);
        applyTheme(pref);
        updateToggleIcons();
    }

    function cycleTheme() {
        var current = getPreference();
        var next = current === 'light' ? 'dark' : current === 'dark' ? 'auto' : 'light';
        setPreference(next);
    }

    function updateToggleIcons() {
        var pref = getPreference();
        var icons = { light: '\u2600\uFE0E', dark: '\uD83C\uDF19', auto: '\u25D4' };
        var labels = { light: 'Light', dark: 'Dark', auto: 'Auto' };
        document.querySelectorAll('.theme-toggle').forEach(function(btn) {
            btn.textContent = icons[pref] || icons.auto;
            btn.title = 'Theme: ' + (labels[pref] || 'Auto') + ' (click to cycle)';
        });
    }

    // Apply immediately to prevent flash
    applyTheme(getPreference());

    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
        if (getPreference() === 'auto') applyTheme('auto');
    });

    // Initialize after DOM ready
    document.addEventListener('DOMContentLoaded', function() {
        updateToggleIcons();
        document.querySelectorAll('.theme-toggle').forEach(function(btn) {
            btn.addEventListener('click', cycleTheme);
        });
    });
})();
