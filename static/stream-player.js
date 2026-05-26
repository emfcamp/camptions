/**
 * WhepPlayer — minimal WebRTC playback client speaking the WHEP protocol.
 *
 * WHEP (https://datatracker.ietf.org/doc/draft-ietf-wish-whep/) negotiates a
 * one-way receive-only stream over a single HTTPS request:
 *
 *   1. Client creates RTCPeerConnection, adds recvonly transceivers, generates
 *      an SDP offer.
 *   2. Client POSTs the offer (Content-Type: application/sdp) to the WHEP URL.
 *   3. Server responds 201 with the SDP answer body.
 *   4. Client sets the remote description and media flows.
 *
 * The player auto-restarts on any failed/disconnected/closed state with capped
 * exponential backoff (1s, 2s, 4s, … max 30s). Callers receive state changes
 * via onStateChange('connecting' | 'connected' | 'failed' | 'closed').
 */

class WhepPlayer {
    /**
     * @param {object} opts
     * @param {string}            opts.url             - WHEP endpoint URL
     * @param {HTMLVideoElement}  opts.videoEl         - target <video> element
     * @param {function}          [opts.onStateChange] - (state: string) => void
     *   States: 'connecting' | 'connected' | 'failed' | 'stopped' | 'closed'
     *   'stopped' fires when maxRetries is exhausted — no further retries.
     * @param {number}            [opts.maxRetries=8]  - give up after this many failed attempts
     */
    constructor({ url, videoEl, onStateChange = () => {}, maxRetries = 8 }) {
        this.url = url;
        this.videoEl = videoEl;
        this.onStateChange = onStateChange;
        this.maxRetries = maxRetries;
        this.pc = null;
        this.resourceUrl = null;
        this.abortController = null;
        this.retryTimer = null;
        this.retryAttempt = 0;
        this.destroyed = false;
        this.lastState = null;
    }

    _setState(state) {
        if (state === this.lastState) return;
        this.lastState = state;
        try { this.onStateChange(state); } catch (e) { console.error(e); }
    }

    async start() {
        if (this.destroyed) return;
        this._setState('connecting');
        this._teardownConnection();

        this.abortController = new AbortController();
        const pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
        });
        this.pc = pc;

        pc.addTransceiver('audio', { direction: 'recvonly' });
        pc.addTransceiver('video', { direction: 'recvonly' });

        const stream = new MediaStream();
        pc.ontrack = (event) => {
            event.streams[0].getTracks().forEach(t => stream.addTrack(t));
            if (this.videoEl.srcObject !== stream) {
                this.videoEl.srcObject = stream;
            }
        };

        pc.onconnectionstatechange = () => {
            if (this.destroyed) return;
            const s = pc.connectionState;
            if (s === 'connected') {
                this.retryAttempt = 0;
                this._setState('connected');
            } else if (s === 'failed' || s === 'disconnected' || s === 'closed') {
                this._scheduleRetry();
            }
        };

        try {
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            await this._waitForIceGathering(pc);

            const res = await fetch(this.url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/sdp' },
                body: pc.localDescription.sdp,
                signal: this.abortController.signal,
            });
            if (!res.ok) {
                throw new Error(`WHEP server returned ${res.status}`);
            }
            const location = res.headers.get('location');
            if (location) {
                try {
                    this.resourceUrl = new URL(location, this.url).toString();
                } catch {
                    this.resourceUrl = location;
                }
            }
            const answer = await res.text();
            await pc.setRemoteDescription({ type: 'answer', sdp: answer });
        } catch (e) {
            if (this.destroyed) return;
            console.warn('WHEP negotiation failed:', e.message);
            this._scheduleRetry();
        }
    }

    _waitForIceGathering(pc) {
        if (pc.iceGatheringState === 'complete') return Promise.resolve();
        return new Promise((resolve) => {
            const onChange = () => {
                if (pc.iceGatheringState === 'complete') {
                    pc.removeEventListener('icegatheringstatechange', onChange);
                    resolve();
                }
            };
            pc.addEventListener('icegatheringstatechange', onChange);
            // Safety cap — some browsers stall on trickle ICE indefinitely.
            setTimeout(() => {
                pc.removeEventListener('icegatheringstatechange', onChange);
                resolve();
            }, 2000);
        });
    }

    _scheduleRetry() {
        if (this.destroyed || this.retryTimer) return;
        if (this.retryAttempt >= this.maxRetries) {
            this._setState('stopped');
            return;
        }
        this._setState('failed');
        const delay = Math.min(30000, 1000 * Math.pow(2, this.retryAttempt));
        this.retryAttempt += 1;
        this.retryTimer = setTimeout(() => {
            this.retryTimer = null;
            if (!this.destroyed) this.start();
        }, delay);
    }

    _teardownConnection() {
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }
        if (this.pc) {
            try { this.pc.close(); } catch {}
            this.pc = null;
        }
        if (this.videoEl) {
            this.videoEl.srcObject = null;
        }
        // Best-effort DELETE of the WHEP resource so the server tears down
        // its end. Fire-and-forget — server may not honour it and we don't
        // want to block reconnect on this.
        if (this.resourceUrl) {
            const url = this.resourceUrl;
            this.resourceUrl = null;
            try { fetch(url, { method: 'DELETE', keepalive: true }); } catch {}
        }
    }

    destroy() {
        this.destroyed = true;
        if (this.retryTimer) {
            clearTimeout(this.retryTimer);
            this.retryTimer = null;
        }
        this._teardownConnection();
        this._setState('closed');
    }
}

window.WhepPlayer = WhepPlayer;
