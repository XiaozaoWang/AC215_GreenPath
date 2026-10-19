import './global.css';

export const metadata = {
    title: 'SmartPath',
    description: 'Personalized scenic cycling: scenery heatmap',
};

export default function RootLayout({ children }) {
    return (
        <html lang="en">
            <body>{children}</body>
        </html>
    );
}
