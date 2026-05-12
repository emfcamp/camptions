/**
 * CaptionsClient — shared WebSocket client and caption renderer.
 *
 * Used by both viewer.html and display.html. Each page passes its own
 * container element and callbacks; this module owns the connection lifecycle,
 * segment map, and block rendering.
 *
 * Protocol expectations (server → client):
 *   connected        { is_live }
 *   venue_live       {}
 *   venue_offline    {}
 *   session_end      {}
 *   committed        { sequence, text, timestamp }
 *   tentative        { text }
 *   keepalive        {}
 *   schedule_update  { now, next }
 */

class CaptionsClient {
    /**
     * @param {object} opts
     * @param {string}      opts.venue             - venue ID for WS URL and history fetch
     * @param {HTMLElement} opts.containerEl        - element that receives .caption-segment / .caption-tentative spans
     * @param {number}      [opts.maxBlocks=500]    - evict oldest block when exceeded
     * @param {function}    [opts.onStatus]         - (cssClass: string, text: string) => void
     * @param {function}    [opts.onSessionStart]   - () => void — fired on venue_live
     * @param {function}    [opts.onSessionEnd]     - () => void — fired on session_end / venue_offline
     * @param {function}    [opts.onScheduleUpdate] - (data) => void
     * @param {function}    [opts.onNewBlock]       - (blockEl: HTMLElement) => void
     */
    constructor({
        venue,
        containerEl,
        maxBlocks = 500,
        onStatus = () => {},
        onSessionStart = () => {},
        onSessionEnd = () => {},
        onScheduleUpdate = () => {},
        onNewBlock = () => {},
    }) {
        this.venue = venue;
        this.containerEl = containerEl;
        this.maxBlocks = maxBlocks;
        this.onStatus = onStatus;
        this.onSessionStart = onSessionStart;
        this.onSessionEnd = onSessionEnd;
        this.onScheduleUpdate = onScheduleUpdate;
        this.onNewBlock = onNewBlock;

        /** @type {Map<number, HTMLElement>} sequence → .caption-segment span */
        this.segmentMap = new Map();
        /** @type {HTMLElement|null} */
        this.tentativeSpan = null;

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
                this.onStatus(
                    data.is_live ? 'live' : 'offline',
                    data.is_live ? 'Connected · Live' : 'Connected · Source Offline',
                );
                return;
            case 'venue_live':
                this.onStatus('live', 'Connected · Live');
                this._clearAll();
                this.onSessionStart();
                return;
            case 'venue_offline':
                this.onStatus('offline', 'Connected · Source Offline');
                this._clearTentative();
                this.onSessionEnd();
                return;
            case 'session_end':
                this._clearTentative();
                this.onSessionEnd();
                return;
            case 'schedule_update':
                this.onScheduleUpdate(data);
                return;
            case 'committed':
                this._onCommitted(data.sequence, data.text);
                return;
            case 'tentative':
                this._onTentative(data.text);
                return;
            default:
                console.warn('CaptionsClient: unhandled message type', data.type);
        }
    }

    // ── Segment rendering ────────────────────────────────────────────────────
    // Committed segments are appended as inline <span>s into containerEl, so
    // captions flow as continuous text rather than one block per segment.
    // The tentative is a trailing <span> that's replaced (or removed) in
    // place; new committed segments are inserted before it.

    _onCommitted(seq, text) {
        if (!text || !text.trim()) return;
        const span = this._ensureSegment(seq);
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

    _ensureSegment(seq) {
        if (this.segmentMap.has(seq)) return this.segmentMap.get(seq);
        const span = document.createElement('span');
        span.className = 'caption-segment';
        span.dataset.seq = seq;
        if (this.tentativeSpan) {
            this.containerEl.insertBefore(span, this.tentativeSpan);
        } else {
            this.containerEl.appendChild(span);
        }
        this.segmentMap.set(seq, span);
        return span;
    }

    _trimSegments() {
        while (this.segmentMap.size > this.maxBlocks) {
            const [seq, span] = this.segmentMap.entries().next().value;
            span.remove();
            this.segmentMap.delete(seq);
        }
    }

    _clearAll() {
        this._clearTentative();
        for (const span of this.segmentMap.values()) span.remove();
        this.segmentMap.clear();
    }

    // ── History ──────────────────────────────────────────────────────────────

    async _loadHistory() {
        try {
            const res = await fetch(`/api/captions/history/${this.venue}?limit=200`);
            if (!res.ok) return;
            const { segments = [] } = await res.json();
            for (const s of segments.filter(s => s.text && s.text.trim())) {
                const seq = s.sequence ?? -(this.segmentMap.size + 1);
                this._onCommitted(seq, s.text);
            }
        } catch (err) {
            console.error('CaptionsClient: history load failed', err);
        }
    }
}

window.CaptionsClient = CaptionsClient;
