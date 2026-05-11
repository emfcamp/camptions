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
     * @param {string}      opts.venue          - venue ID for WS URL and history fetch
     * @param {HTMLElement} opts.containerEl     - element that receives .caption-block children
     * @param {number}      [opts.maxBlocks=500] - evict oldest block+map entry when exceeded
     * @param {function}    [opts.onStatus]      - (cssClass: string, text: string) => void
     * @param {function}    [opts.onSessionStart] - () => void — fired on venue_live
     * @param {function}    [opts.onSessionEnd]   - () => void — fired on session_end / venue_offline
     * @param {function}    [opts.onScheduleUpdate] - (data) => void
     * @param {function}    [opts.onNewBlock]     - (blockEl: HTMLElement) => void — after each new committed block
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

        /** @type {Map<number, HTMLElement>} sequence → .caption-block element */
        this.segmentMap = new Map();

        /** @type {HTMLElement|null} */
        this.tentativeBlock = null;

        this.ws = null;
        this.reconnectAttempts = 0;
        this._reconnectTimer = null;
        this._destroyed = false;
    }

    /** Load history then open the WebSocket. */
    async init() {
        await this._loadHistory();
        this._connect();
    }

    /** Close the WS and cancel any pending reconnect. */
    destroy() {
        this._destroyed = true;
        if (this._reconnectTimer !== null) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
    }

    // ── WebSocket lifecycle ──────────────────────────────────────────────────

    _wsUrl() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${location.host}/api/captions/stream/${this.venue}`;
    }

    _connect() {
        if (this._destroyed) return;
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }
        this.onStatus('reconnecting', 'Connecting...');
        this.ws = new WebSocket(this._wsUrl());
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
                this._onCommitted(data.sequence, data.text, data.timestamp);
                return;
            case 'tentative':
                this._onTentative(data.text);
                return;
            default:
                console.warn('CaptionsClient: unhandled message type', data.type);
        }
    }

    // ── Segment rendering ────────────────────────────────────────────────────

    _onCommitted(seq, text, timestamp) {
        if (!text || !text.trim()) return;
        this._clearTentative();
        const block = this._ensureBlock(seq);
        block.querySelector('.block-text').textContent = text.trim();
        this._trimBlocks();
        this.onNewBlock(block);
    }

    _onTentative(text) {
        if (!text || !text.trim()) {
            this._clearTentative();
            return;
        }
        if (!this.tentativeBlock) {
            this.tentativeBlock = document.createElement('div');
            this.tentativeBlock.className = 'caption-block caption-block--tentative';
            const p = document.createElement('p');
            p.className = 'block-text block-tentative';
            this.tentativeBlock.appendChild(p);
            this.containerEl.appendChild(this.tentativeBlock);
        }
        this.tentativeBlock.querySelector('.block-text').textContent = text.trim();
        this.onNewBlock(this.tentativeBlock);
    }

    _clearTentative() {
        if (this.tentativeBlock) {
            this.tentativeBlock.remove();
            this.tentativeBlock = null;
        }
    }

    /** Returns existing block for seq, or creates and appends a new one. */
    _ensureBlock(seq) {
        if (this.segmentMap.has(seq)) {
            return this.segmentMap.get(seq);
        }
        const block = document.createElement('div');
        block.className = 'caption-block';
        block.dataset.seq = seq;
        const p = document.createElement('p');
        p.className = 'block-text';
        block.appendChild(p);
        // Insert before tentative block if present, otherwise append.
        if (this.tentativeBlock) {
            this.containerEl.insertBefore(block, this.tentativeBlock);
        } else {
            this.containerEl.appendChild(block);
        }
        this.segmentMap.set(seq, block);
        return block;
    }

    /** Evict the oldest block from the DOM and map when over maxBlocks. */
    _trimBlocks() {
        while (this.segmentMap.size > this.maxBlocks) {
            const [oldestSeq, oldestBlock] = this.segmentMap.entries().next().value;
            oldestBlock.remove();
            this.segmentMap.delete(oldestSeq);
        }
    }

    /** Remove all committed blocks and clear the segment map. */
    _clearAll() {
        this._clearTentative();
        for (const block of this.segmentMap.values()) {
            block.remove();
        }
        this.segmentMap.clear();
    }

    // ── History ──────────────────────────────────────────────────────────────

    async _loadHistory() {
        try {
            const res = await fetch(`/api/captions/history/${this.venue}?limit=200`);
            if (!res.ok) return;
            const data = await res.json();
            const segments = (data.segments || []).filter(s => s.text && s.text.trim());
            for (const s of segments) {
                // History segments may not have sequence numbers in older DB rows;
                // use a synthetic negative sequence to avoid colliding with live seqs.
                const seq = s.sequence ?? -(this.segmentMap.size + 1);
                this._onCommitted(seq, s.text);
            }
        } catch (err) {
            console.error('CaptionsClient: history load failed', err);
        }
    }
}

// Export for use as a plain <script> tag (no module system required).
window.CaptionsClient = CaptionsClient;
