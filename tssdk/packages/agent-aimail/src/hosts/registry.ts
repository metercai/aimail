// Host registry — single source of truth for "which agent hosts this package
// supports, and how each is loaded / detected".
//
// Adding a 4th host type = add one entry here + one src/{host}/ entry dir.
// core / mail / identity / autoBind / board / skill / pull stay host-agnostic
// and are never duplicated.
//
// The per-host activation entry (dist/{host}/index.js) is what the host's own
// loader picks up:
//   openclaw  → openclaw.plugin.json + package.json openclaw.extensions
//   dsh       → cordis.patch.yml (self-mounting bundle, entries → ./dist/dsh/*)
//   pi        → package.json pi.extensions → ./dist/pi/index.js
//
// This registry is the DECLARATIVE contract. detect.ts turns it into a runtime
// assertion; each host entry calls activateHost() to verify it is on the host
// it was loaded by (catch "declared host != actual host" half-failures).

export interface HostSpec {
  /** host key, matches the src/{host}/ entry dir + dist/{host}/ output. */
  host: 'openclaw' | 'dsh' | 'pi' | (string & {})
  /** human label. */
  label: string
  /** npm package(s) whose resolvability proves the host SDK is present. */
  sdkSpecs: string[]
  /** host config dir under $HOME (a second detection signal). */
  configDir: string
  /** the entry subpath the host loader consumes (contract, mirrored in CLI). */
  entry: string
  /** register-cli subpath (CLI 注册命令入口, contract mirrored in hosts.rs). */
  registerCli: string
  /** the peer dependency the host must provide (all optional at install). */
  peer: string
}

export const HOSTS: Record<string, HostSpec> = {
  openclaw: {
    host: 'openclaw',
    label: 'OpenClaw',
    sdkSpecs: ['openclaw/plugin-sdk/plugin-entry'],
    configDir: '.openclaw',
    entry: 'dist/openclaw/index.js',
    registerCli: 'dist/openclaw/register-cli.js',
    peer: 'openclaw',
  },
  dsh: {
    host: 'dsh',
    label: 'DeepSeek Harness (dsh)',
    sdkSpecs: ['@deepseek-ai/cordis', '@deepseek-ai/dsh-agent'],
    configDir: '.dsh',
    entry: 'dist/dsh/index.js',
    registerCli: 'dist/dsh/register-cli.js',
    peer: '@deepseek-ai/cordis',
  },
  pi: {
    host: 'pi',
    label: 'Pi coding agent',
    sdkSpecs: ['@earendil-works/pi-coding-agent'],
    configDir: '.pi',
    entry: 'dist/pi/index.js',
    registerCli: 'dist/pi/register-cli.js',
    peer: '@earendil-works/pi-coding-agent',
  },
}

export const HOST_KEYS = Object.keys(HOSTS)
