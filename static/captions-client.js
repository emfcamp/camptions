/**
 * CaptionsClient — shared WebSocket client and caption renderer.
 *
 * Used by both viewer.html and display.html. Each page passes its own
 * container element and callbacks; this module owns the connection lifecycle,
 * segment map, and block rendering.
 *
 * Segments are keyed by `${session_id}:${sequence}` so committed segments
 * from a fresh session never overwrite history slots from an older session
 * (per-session sequences both restart at 1). venue_live only clears the
 * display when the session_id genuinely changes — a backend WL reconnect
 * within the same Pi session leaves the captions in place.
 *
 * Protocol expectations (server → client):
 *   connected               { session_id, is_live, transcription_enabled }
 *   venue_live              { session_id }
 *   venue_offline           {}
 *   session_end             { session_id }
 *   committed               { session_id, sequence, text, timestamp }
 *   tentative               { session_id, text }
 *   transcription_disabled  {}
 *   transcription_enabled   {}
 *   keepalive               {}
 *   schedule_update         { now, next }
 */

class CaptionsClient {
    /**
     * @param {object} opts
     * @param {string}      opts.venue             - venue ID for WS URL and history fetch
     * @param {HTMLElement} opts.containerEl        - element that receives .caption-segment / .caption-tentative spans
     * @param {number}      [opts.maxBlocks=500]    - evict oldest block when exceeded
     * @param {number}      [opts.historyLimit=200] - segments requested from /history on first load
     * @param {function}    [opts.onStatus]         - (cssClass: string, text: string) => void
     * @param {function}    [opts.onSessionStart]   - () => void — fired on venue_live
     * @param {function}    [opts.onSessionEnd]     - () => void — fired on session_end / venue_offline
     * @param {function}    [opts.onScheduleUpdate] - (data) => void
     * @param {function}    [opts.onNewBlock]       - (blockEl: HTMLElement) => void
     * @param {function}    [opts.onPausedChange]   - (paused: bool) => void — fired when admin toggles transcription
     * @param {function}    [opts.onTrim]           - (heightDelta: number) => void — fired when front segments are evicted; delta is the height (px) the container lost so callers can compensate scroll/transform instantly
     */
    constructor({
        venue,
        containerEl,
        maxBlocks = 500,
        historyLimit = 200,
        onStatus = () => {},
        onSessionStart = () => {},
        onSessionEnd = () => {},
        onScheduleUpdate = () => {},
        onNewBlock = () => {},
        onPausedChange = () => {},
        onTrim = () => {},
    }) {
        this.venue = venue;
        this.containerEl = containerEl;
        this.maxBlocks = maxBlocks;
        this.historyLimit = historyLimit;
        this.onStatus = onStatus;
        this.onSessionStart = onSessionStart;
        this.onSessionEnd = onSessionEnd;
        this.onScheduleUpdate = onScheduleUpdate;
        this.onNewBlock = onNewBlock;
        this.onPausedChange = onPausedChange;
        this.onTrim = onTrim;

        /** @type {Map<string, HTMLElement>} "session_id:sequence" → .caption-segment span */
        this.segmentMap = new Map();
        /** @type {HTMLElement|null} */
        this.tentativeSpan = null;
        /** @type {string|null} session_id currently rendered live; null until we see one */
        this.currentSessionId = null;
        /** @type {boolean} true when admin has disabled transcription for this venue */
        this.paused = false;

        this.ws = null;
        this.reconnectAttempts = 0;
        this._reconnectTimer = null;
        this._destroyed = false;
    }

    /** Load history, open the WebSocket, and register page-lifecycle hooks. */
    async init() {
        this._visibilityHandler = () => {
            if (document.visibilityState === 'visible') {
                if (!this.ws || this.ws.readyState !== WebSocket.OPEN) this.reconnect();
            }
        };
        document.addEventListener('visibilitychange', this._visibilityHandler);
        await this._loadHistory();
        this._connect();
    }

    /** Close the WS, cancel any pending reconnect, and remove event listeners. */
    destroy() {
        this._destroyed = true;
        document.removeEventListener('visibilitychange', this._visibilityHandler);
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
    }

    /** Reset backoff and reconnect immediately. */
    reconnect() {
        this.reconnectAttempts = 0;
        this._connect();
    }

    // ── WebSocket lifecycle ──────────────────────────────────────────────────

    _connect() {
        if (this._destroyed) return;
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }
        this.onStatus('reconnecting', 'Connecting...');
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${proto}//${location.host}/api/captions/stream/${this.venue}`);
        this.ws.onopen = () => { this.reconnectAttempts = 0; };
        this.ws.onmessage = e => this._handleMessage(JSON.parse(e.data));
        this.ws.onclose = () => {
            this.onStatus('disconnected', 'Disconnected');
            this._scheduleReconnect();
        };
        this.ws.onerror = console.error;
    }

    _scheduleReconnect() {
        if (this._destroyed) return;
        this.reconnectAttempts += 1;
        const delay = Math.min(1000 * 2 ** Math.min(this.reconnectAttempts - 1, 5), 30000);
        this._reconnectTimer = setTimeout(() => this._connect(), delay);
    }

    // ── Message dispatch ─────────────────────────────────────────────────────

    _handleMessage(data) {
        switch (data.type) {
            case 'keepalive':
                return;
            case 'connected':
                if (data.session_id) this.currentSessionId = data.session_id;
                if (typeof data.transcription_enabled === 'boolean') {
                    this._setPaused(!data.transcription_enabled);
                }
                if (!this.paused) {
                    this.onStatus(
                        data.is_live ? 'live' : 'offline',
                        data.is_live ? 'Connected · Live' : 'Connected · Source Offline',
                    );
                }
                return;
            case 'venue_live':
                this._adoptSession(data.session_id);
                if (!this.paused) {
                    this.onStatus('live', 'Connected · Live');
                    this.onSessionStart();
                }
                return;
            case 'venue_offline':
                this._clearTentative();
                if (!this.paused) {
                    this.onStatus('offline', 'Connected · Source Offline');
                    this.onSessionEnd();
                }
                return;
            case 'session_end':
                this._clearTentative();
                this.onSessionEnd();
                return;
            case 'schedule_update':
                this.onScheduleUpdate(data);
                return;
            case 'committed':
                this._onCommitted(data.session_id, data.sequence, data.text);
                return;
            case 'tentative':
                this._onTentative(data.text);
                return;
            case 'transcription_disabled':
                this._setPaused(true);
                return;
            case 'transcription_enabled':
                this._setPaused(false);
                return;
            default:
                console.warn('CaptionsClient: unhandled message type', data.type);
        }
    }

    _setPaused(paused) {
        if (this.paused === paused) return;
        this.paused = paused;
        if (paused) {
            this._clearTentative();
            this.onStatus('paused', 'Transcription paused');
        } else {
            // After un-pausing we revert to "offline" until the next
            // venue_live/connected message refines it. A new Pi session is
            // also a new session_id, so committed history is preserved.
            this.onStatus('offline', 'Connected · Source Offline');
        }
        this.onPausedChange(paused);
    }

    // ── Session tracking ─────────────────────────────────────────────────────
    // Segments are keyed by (session_id, sequence), so a new session never
    // overwrites an older session's slots. We never clear on session
    // change — viewers want continuity across Pi reconnects, talk
    // transitions, etc. _trimSegments still bounds the in-DOM count.
    _adoptSession(sessionId) {
        if (!sessionId) return;
        this.currentSessionId = sessionId;
    }

    // ── Segment rendering ────────────────────────────────────────────────────
    // Committed segments are appended as inline <span>s into containerEl, so
    // captions flow as continuous text rather than one block per segment.
    // The tentative is a trailing <span> that's replaced (or removed) in
    // place; new committed segments are inserted before it.

    _onCommitted(sessionId, seq, text) {
        if (!text || !text.trim()) return;
        this._clearTentative();
        const span = this._ensureSegment(sessionId, seq);
        span.textContent = text.trim() + ' ';
        this._trimSegments();
        this.onNewBlock(span);
    }

    _onTentative(text) {
        if (!text || !text.trim()) {
            this._clearTentative();
            return;
        }
        if (!this.tentativeSpan) {
            this.tentativeSpan = document.createElement('span');
            this.tentativeSpan.className = 'caption-tentative';
            this.containerEl.appendChild(this.tentativeSpan);
        }
        this.tentativeSpan.textContent = text.trim();
        this.onNewBlock(this.tentativeSpan);
    }

    _clearTentative() {
        if (this.tentativeSpan) {
            this.tentativeSpan.remove();
            this.tentativeSpan = null;
        }
    }

    _ensureSegment(sessionId, seq) {
        const key = `${sessionId ?? '_'}:${seq}`;
        if (this.segmentMap.has(key)) return this.segmentMap.get(key);
        const span = document.createElement('span');
        span.className = 'caption-segment';
        span.dataset.sessionId = sessionId ?? '';
        span.dataset.seq = seq;
        if (this.tentativeSpan) {
            this.containerEl.insertBefore(span, this.tentativeSpan);
        } else {
            this.containerEl.appendChild(span);
        }
        this.segmentMap.set(key, span);
        return span;
    }

    _trimSegments() {
        if (this.segmentMap.size <= this.maxBlocks) return;
        // Measure container height before/after eviction so the caller can
        // snap scroll/transform instantly and avoid a visible jump caused
        // by the container shrinking from the top.
        const heightBefore = this.containerEl.offsetHeight;
        while (this.segmentMap.size > this.maxBlocks) {
            const [key, span] = this.segmentMap.entries().next().value;
            span.remove();
            this.segmentMap.delete(key);
        }
        const delta = heightBefore - this.containerEl.offsetHeight;
        if (delta > 0) this.onTrim(delta);
    }

    _clearAll() {
        this._clearTentative();
        for (const span of this.segmentMap.values()) span.remove();
        this.segmentMap.clear();
    }

    // ── History ──────────────────────────────────────────────────────────────

    async _loadHistory() {
        if (this.historyLimit <= 0) return;
        try {
            const url = `/api/captions/history/${this.venue}?limit=${this.historyLimit}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const { segments = [] } = await res.json();
            for (const s of segments.filter(s => s.text && s.text.trim())) {
                const seq = s.sequence ?? -(this.segmentMap.size + 1);
                this._onCommitted(s.session_id ?? null, seq, s.text);
            }
        } catch (err) {
            console.error('CaptionsClient: history load failed', err);
        }
    }
}

window.CaptionsClient = CaptionsClient;
