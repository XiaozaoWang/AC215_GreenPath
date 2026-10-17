import { useEffect } from 'react';
import SettingsContext, { applyTheme, themeModes } from './SettingsContext';

export default function SettingsProvider({ children }) {

  // Setup Component
  useEffect(() => {
    applyTheme('light');
  }, []);

  return <SettingsContext.Provider value={SettingsContext}>{children}</SettingsContext.Provider>
}