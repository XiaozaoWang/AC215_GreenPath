import React from 'react';

export const AppTheme = 'darcula' | 'bright' | 'auto';
export const Spaciness = 'eco' | 'roomy' | 'cozy';

export const ThemeMode = {
    Dark: 'dark',
    Light: 'light',
    Auto: 'auto',
}

export const themeModes = {
    bright: ThemeMode.Light,
    darcula: ThemeMode.Dark,
    auto: ThemeMode.Auto,
};

export const appThemes = {
    [ThemeMode.Light]: 'bright',
    [ThemeMode.Dark]: 'darcula',
    [ThemeMode.Auto]: 'auto',
};

export function applyTheme(themeMode, el) {
    console.log("applyTheme...: " + themeMode)
    if (!el) {
        el = document.documentElement;
    }
    if (el.classList.contains(themeMode)) {
        return;
    }

    if (themeMode === ThemeMode.Dark) {
        el.classList.remove(ThemeMode.Light);
        el.classList.remove(ThemeMode.Auto);
    } else if (themeMode === ThemeMode.Light) {
        el.classList.add(ThemeMode.Light);
        el.classList.remove(ThemeMode.Auto);
    } else {
        el.classList.remove(ThemeMode.Light);
        el.classList.add(ThemeMode.Auto);
    }
}

const defaultSettingContextData = {
    spaciness: Spaciness.eco,
};

const SettingContext = React.createContext(defaultSettingContextData);

export default SettingContext;