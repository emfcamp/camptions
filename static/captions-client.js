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
        lineMode = false,
        maxLines = 20,
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
        // Line mode (display only): committed text is baked into fixed
        // single-row .caption-line blocks at their natural wrap points, and
        // whole lines are purged off the top past maxLines. Because each line
        // is an independent block, purging never reflows the lines below — they
        // keep their exact wrapping and just shift up. Default off so the
        // viewer keeps its continuous inline flow.
        this._lineMode = lineMode;
        this.maxLines = maxLines;
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

        /** @type {boolean} when true, tentative updates are buffered, not rendered */
        this._tentativePaused = false;
        /** @type {string|undefined} latest tentative text withheld while paused */
        this._pendingTentative = undefined;

        // Line-mode state.
        /** @type {HTMLElement[]} frozen + in-progress .caption-line blocks, oldest first */
        this.lines = [];
        /** @type {HTMLElement|null} the line currently being filled */
        this.currentLine = null;
        /** @type {Set<string>} recently-seen committed keys, for dedup (bounded) */
        this._seenCommitted = new Set();
        /** @type {number} cached single-row height in px; 0 = needs measuring */
        this._singleLineHeight = 0;

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
        if (this._lineMode) {
            // Font size is viewport-relative, so the single-row height changes
            // when the window resizes — drop the cached value so the next
            // wrap measurement re-reads it.
            this._resizeHandler = () => { this._singleLineHeight = 0; };
            window.addEventListener('resize', this._resizeHandler);
        }
        await this._loadHistory();
        this._connect();
    }

    /** Close the WS, cancel any pending reconnect, and remove event listeners. */
    destroy() {
        this._destroyed = true;
        document.removeEventListener('visibilitychange', this._visibilityHandler);
        if (this._resizeHandler) window.removeEventListener('resize', this._resizeHandler);
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

    // ── Tentative render gating ──────────────────────────────────────────────
    // Callers driving a scroll animation can pause tentative rendering for the
    // duration of the glide. Tentative messages keep arriving but are buffered
    // (latest wins) instead of written to the DOM, so the scrolling layer isn't
    // repainted mid-motion. resumeTentative() flushes the latest buffered text.

    /** Buffer tentative updates instead of rendering them. */
    pauseTentative() {
        this._tentativePaused = true;
    }

    /** Resume tentative rendering and flush the latest buffered update, if any. */
    resumeTentative() {
        if (!this._tentativePaused) return;
        this._tentativePaused = false;
        if (this._pendingTentative !== undefined) {
            const text = this._pendingTentative;
            this._pendingTentative = undefined;
            this._onTentative(text);
        }
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
                if (this.paused) {
                    // _setPaused early-returns when the paused state is
                    // unchanged (it survives reconnects), so restore the paused
                    // status explicitly — otherwise a reconnect while paused
                    // leaves the status stuck on "Connecting…".
                    this.onStatus('paused', 'Transcription paused');
                } else {
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
                this._setPaused(false, data.is_live);
                return;
            default:
                console.warn('CaptionsClient: unhandled message type', data.type);
        }
    }

    _setPaused(paused, isLive = false) {
        if (this.paused === paused) return;
        this.paused = paused;
        if (paused) {
            this._clearTentative();
            this.onStatus('paused', 'Transcription paused');
        } else if (isLive) {
            // The source was streaming all along — go straight back to Live.
            // Without the server's is_live hint we'd stick on "offline" until
            // the next venue_live, which never comes for an unchanged session.
            this.onStatus('live', 'Connected · Live');
        } else {
            // Source genuinely offline; the next venue_live/connected refines it.
            // A new Pi session is also a new session_id, so committed history
            // is preserved.
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
        // A committed segment supersedes any buffered tentative — drop it so we
        // don't later flush stale in-progress text that overlaps this segment.
        this._pendingTentative = undefined;
        this._clearTentative();
        if (this._lineMode) {
            const key = `${sessionId ?? '_'}:${seq}`;
            if (this._seenCommitted.has(key)) return;
            this._rememberCommitted(key);
            this._appendCommittedText(text.trim());
            this._trimLines();
            this.onNewBlock(this.currentLine);
            return;
        }
        const span = this._ensureSegment(sessionId, seq);
        span.textContent = text.trim() + ' ';
        this._trimSegments();
        this.onNewBlock(span);
    }

    _onTentative(text) {
        // Never render in-progress text while transcription is paused — the
        // server shouldn't send it, but a late throttled flush could race the
        // pause, and it must not appear under the paused banner.
        if (this.paused) return;
        // While a glide is animating, buffer the latest tentative instead of
        // rendering it; resumeTentative() flushes it once the glide settles.
        if (this._tentativePaused) {
            this._pendingTentative = text;
            return;
        }
        if (!text || !text.trim()) {
            this._clearTentative();
            return;
        }
        if (!this.tentativeSpan) {
            this.tentativeSpan = document.createElement('span');
            this.tentativeSpan.className = 'caption-tentative';
            // In line mode the tentative trails the committed text on the
            // current line so the flow stays continuous; otherwise it sits at
            // the end of the inline container.
            const parent = this._lineMode ? this._ensureCurrentLine() : this.containerEl;
            parent.appendChild(this.tentativeSpan);
        }
        // In line mode committed words carry no trailing space, so insert one
        // between the firm text and the tentative when they share a line.
        const sep = (this._lineMode && this.tentativeSpan.previousSibling) ? ' ' : '';
        this.tentativeSpan.textContent = sep + text.trim();
        this.onNewBlock(this.tentativeSpan);
    }

    // ── Line-mode rendering ──────────────────────────────────────────────────
    // Committed words are appended to the current line; when a word tips the
    // line onto a second visual row, it's moved to a fresh line so every
    // committed line is exactly one row tall. Whole lines past maxLines are
    // purged off the top — and because each line is its own block, that purge
    // never reflows the surviving lines (they keep their wrapping and shift up).

    _ensureCurrentLine() {
        if (!this.currentLine) this._startNewLine();
        return this.currentLine;
    }

    _startNewLine() {
        const line = document.createElement('div');
        line.className = 'caption-line';
        this.containerEl.appendChild(line);
        this.lines.push(line);
        this.currentLine = line;
        return line;
    }

    _appendCommittedText(text) {
        this._ensureCurrentLine();
        for (const word of text.split(/\s+/)) {
            if (word) this._appendWord(word);
        }
    }

    _appendWord(word) {
        const line = this._ensureCurrentLine();
        const lineWasEmpty = line.firstChild === null;
        const node = document.createTextNode(lineWasEmpty ? word : ' ' + word);
        line.appendChild(node);
        // If the word pushed the line onto a second row, move it to a new line.
        // A lone word wider than the row can't be split, so leave it in place.
        if (!lineWasEmpty && this._isWrapped(line)) {
            line.removeChild(node);
            this._startNewLine();
            this.currentLine.appendChild(document.createTextNode(word));
        }
    }

    _isWrapped(line) {
        const single = this._singleRowHeight();
        return single > 0 && line.offsetHeight > single * 1.5;
    }

    _singleRowHeight() {
        if (!this._singleLineHeight) {
            this._singleLineHeight = parseFloat(getComputedStyle(this.containerEl).lineHeight) || 0;
        }
        return this._singleLineHeight;
    }

    _rememberCommitted(key) {
        this._seenCommitted.add(key);
        // Bound the dedup set; it only needs to cover the history/live overlap
        // window, not the whole session. Evict oldest (insertion order).
        if (this._seenCommitted.size > 200) {
            this._seenCommitted.delete(this._seenCommitted.values().next().value);
        }
    }

    _trimLines() {
        if (this.lines.length <= this.maxLines) return;
        const heightBefore = this.containerEl.offsetHeight;
        while (this.lines.length > this.maxLines) {
            this.lines.shift().remove();
        }
        const delta = heightBefore - this.containerEl.offsetHeight;
        if (delta > 0) this.onTrim(delta);
    }

    /**
     * Re-bake every committed line at the current font/width metrics. Wrap
     * points are frozen at bake time, so they go stale when the font size or
     * the viewport width changes — call this to recompute them. The tentative
     * is preserved. Scroll position is the caller's responsibility (line
     * breaks move, so only the caller knows how to re-anchor the view).
     */
    reflowLines() {
        if (!this._lineMode) return;
        const tentative = this.tentativeSpan ? this.tentativeSpan.textContent.trim() : null;
        this._clearTentative();
        // Reconstruct the committed text from the lines (joined with spaces —
        // the breaks only ever fall between words) and rebuild from scratch.
        const text = this.lines.map(l => l.textContent).join(' ').replace(/\s+/g, ' ').trim();
        for (const line of this.lines) line.remove();
        this.lines = [];
        this.currentLine = null;
        this._singleLineHeight = 0; // metrics changed — force a re-measure
        if (text) this._appendCommittedText(text);
        if (tentative) this._onTentative(tentative);
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
        if (this._lineMode) {
            for (const line of this.lines) line.remove();
            this.lines = [];
            this.currentLine = null;
            this._seenCommitted.clear();
        }
    }

    // ── History ──────────────────────────────────────────────────────────────

    async _loadHistory() {
        if (this.historyLimit <= 0) return;
        try {
            const url = `/api/captions/history/${this.venue}?limit=${this.historyLimit}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const { segments = [] } = await res.json();
            // Line mode freezes wrap points at bake time, so we must bake with
            // the real font metrics. Baking before the web font swaps in would
            // freeze breaks at the fallback font's wrap points — off by ~a word
            // once the real font loads. Wait for fonts before baking history.
            if (this._lineMode && document.fonts?.ready) {
                await document.fonts.ready;
            }
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
