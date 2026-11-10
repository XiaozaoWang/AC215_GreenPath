'use client';

import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import DataService from '@/services/DataService';

const STORAGE_KEY = 'greenpath-ride-chat';

function safeSourceUrl(value) {
    try {
        const url = new URL(value);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
}

function Sources({ sources = [] }) {
    const unique = sources.filter((source, index) => sources.findIndex(other =>
        (other.metadata?.source_id || other.metadata?.source_url || other.chunk_id) ===
        (source.metadata?.source_id || source.metadata?.source_url || source.chunk_id)) === index);
    if (!unique.length) return null;
    return <details className="ride-sources">
        <summary>Sources <span>{unique.length}</span></summary>
        <ul>{unique.map((source, index) => {
            const meta = source.metadata || {};
            const href = safeSourceUrl(meta.source_url);
            const title = meta.title || meta.publisher || 'Cycling reference';
            return <li key={source.chunk_id || index}>
                {href ? <a href={href} target="_blank" rel="noopener noreferrer">{title} ↗</a> : <strong>{title}</strong>}
                <small>{[meta.publisher, meta.region, meta.jurisdiction].filter(Boolean).join(' · ')}</small>
                {meta.scope_note && <small>{meta.scope_note}</small>}
            </li>;
        })}</ul>
    </details>;
}

export default function RideAssistant({ open, onClose }) {
    const [messages, setMessages] = useState([]);
    const [draft, setDraft] = useState('');
    const [pending, setPending] = useState(false);
    const [hydrated, setHydrated] = useState(false);
    const scrollRef = useRef(null);
    const inputRef = useRef(null);
    const panelRef = useRef(null);
    const sendingRef = useRef(false);

    useEffect(() => {
        try {
            const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]');
            if (Array.isArray(saved)) setMessages(saved.filter(message =>
                ['user', 'assistant'].includes(message.role) && typeof message.text === 'string' && typeof message.id === 'string'));
        } catch { /* History remains available in memory if browser storage is unavailable. */ }
        setHydrated(true);
    }, []);

    useEffect(() => {
        if (!hydrated) return;
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages)); } catch { /* Keep the in-memory history. */ }
    }, [messages, hydrated]);

    useEffect(() => {
        if (!open) return;
        const frame = requestAnimationFrame(() => inputRef.current?.focus());
        return () => cancelAnimationFrame(frame);
    }, [open]);

    useEffect(() => {
        if (!open || !scrollRef.current) return;
        scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight,
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }, [open, messages, pending]);

    async function sendQuestion(value, retryId) {
        const question = value.trim();
        if (!question || question.length > 2000 || sendingRef.current) return;
        sendingRef.current = true;
        setPending(true);
        setDraft('');
        setMessages(previous => retryId ? previous.filter(message => message.id !== retryId)
            : [...previous, { id: crypto.randomUUID(), role: 'user', text: question }]);
        try {
            // The API is stateless: send this question only, never imply model memory.
            const result = await DataService.RagChat(question);
            if (typeof result.answer !== 'string' || !result.answer.trim()) throw new Error('No answer came back. Please try again.');
            setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'assistant', text: result.answer,
                sources: Array.isArray(result.sources) ? result.sources : [] }]);
        } catch (error) {
            setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'assistant', error: true,
                text: error.message || 'Could not connect. Please try again.', question }]);
        } finally {
            sendingRef.current = false;
            setPending(false);
        }
    }

    function submit(event) {
        event.preventDefault();
        sendQuestion(draft);
    }

    function handleKeys(event) {
        if (event.key === 'Escape') { event.preventDefault(); onClose(); }
        if (event.key !== 'Tab') return;
        const controls = [...panelRef.current.querySelectorAll('button:not(:disabled), textarea, a[href], summary')]
            .filter(element => element.getClientRects().length);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }

    return <div className="ride-chat-layer" hidden={!open}>
        <section id="ride-assistant" className="ride-chat" role="dialog" aria-modal="true"
            aria-labelledby="ride-title" ref={panelRef} onKeyDown={handleKeys}>
            <header className="ride-chat-header">
                <span className="ride-avatar" aria-hidden="true">↗</span>
                <div><h1 id="ride-title">Ride Assistant</h1><p>A little guidance for your next ride</p></div>
                <button className="ride-close" onClick={onClose} aria-label="Close Ride Assistant">×</button>
            </header>
            <div className="ride-messages" ref={scrollRef} role="log" aria-label="Ride conversation" aria-live="polite" aria-relevant="additions">
                <div className="ride-welcome">
                    <span className="ride-eyebrow">GOOD RIDES START HERE</span>
                    <h2>Where will curiosity<br />take you?</h2>
                    <p>Ask about bike prep, riding tips, or cycling rules. I’ll find guidance with sources you can check.</p>
                    {!messages.length && <div className="ride-suggestions">
                        {['What should I check before a ride?', 'How can I ride more comfortably?'].map(question =>
                            <button key={question} disabled={pending} onClick={() => sendQuestion(question)}>{question} <span aria-hidden="true">↗</span></button>)}
                    </div>}
                    <p className="ride-memory-note">Each question is answered on its own. Include any details you want me to use.</p>
                </div>
                {messages.map(message => <article key={message.id} className={`ride-message ride-message-${message.role}${message.error ? ' ride-message-error' : ''}`}>
                    <span className="ride-message-label">{message.role === 'user' ? 'You' : 'Ride Assistant'}</span>
                    <div className="ride-bubble">
                        {message.role === 'user' ? <p>{message.text}</p> : message.error ? <>
                            <p role="alert">{message.text}</p>
                            <button className="ride-retry" disabled={pending} onClick={() => sendQuestion(message.question, message.id)}>Try again ↻</button>
                        </> : <div className="ride-markdown"><ReactMarkdown skipHtml components={{
                            a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
                            img: () => null,
                        }}>{message.text}</ReactMarkdown></div>}
                        {message.role === 'assistant' && !message.error && <Sources sources={message.sources} />}
                    </div>
                </article>)}
                {pending && <div className="ride-message ride-message-assistant" role="status" aria-label="Ride Assistant is preparing an answer">
                    <span className="ride-message-label">Ride Assistant</span>
                    <div className="ride-bubble ride-typing"><span /><span /><span /><small>Finding guidance…</small></div>
                </div>}
            </div>
            <form className="ride-composer" onSubmit={submit}>
                <div className="ride-input-wrap">
                    <textarea ref={inputRef} value={draft} onChange={event => setDraft(event.target.value)}
                        onKeyDown={event => {
                            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(event); }
                        }}
                        rows={2} maxLength={2000} aria-label="Message Ride Assistant" placeholder="Ask about your next ride…" />
                    <button type="submit" disabled={pending || !draft.trim()} aria-label="Send message">↑</button>
                </div>
                <p>Guidance, not live route conditions. Check local rules.</p>
            </form>
        </section>
    </div>;
}
