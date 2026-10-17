'use client'

// Global shell: only provides the settings context; the page handles its own three-column layout
import SettingsProvider from '@/contexts/SettingsProvider';

export default function MainLayout({ children }) {
    return (
        <SettingsProvider>
            <div id="__next" className="antialiased">
                {children}
            </div>
        </SettingsProvider>
    )
}
