import './global.css';
// Layout
import MainLayout from '@/layouts/MainLayout';

export const metadata = {
    title: 'SmartPath',
    description: 'Personalized scenic cycling: scenery heatmap',
}

export default function RootLayout({ children }) {
    return (
        <html lang="en">
            <head>
                <meta charSet="utf-8" />
                <link href="/seas-logo.svg" rel="icon" type="image/svg+xml"></link>
                <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=0, maximum-scale=1, minimum-scale=1" />
                <title>SmartPath</title>
            </head>
            <body>
                <MainLayout>
                    {children}
                </MainLayout>
            </body>
        </html>
    )
}