import { redirect } from 'next/navigation';

export const dynamic = 'force-dynamic';

// Keep old bookmarks opening the conversation inside the map's phone UI.
export default function Assistant() {
    redirect('/?assistant=open');
}
