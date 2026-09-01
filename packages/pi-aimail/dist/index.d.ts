import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
export declare const INBOUND_PATH = "/aimail/inbound";
export interface PiAimailOptions {
    /** Inbound listener port (default 9101). */
    inboundPort?: number;
}
export default function piAimail(pi: ExtensionAPI, options?: PiAimailOptions): void;
//# sourceMappingURL=index.d.ts.map