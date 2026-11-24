/**
 * Bundle smoke test — the closest thing to the host that CI can run.
 *
 * The built `main.js` is loaded into plain Node with a fake `pi`, the service is
 * started, one queue round is served, and the script asserts what the
 * *bundle* did: it pulled the queue, allocated a session for the thread, wrote
 * the mail under the deterministic id, and only then acked. A second scenario
 * makes the host refuse the write and asserts the mail is **not** acked.
 *
 * This does not replace an installed-plugin check (permissions, sandbox,
 * service supervision are the host's) — it pins the wiring between the host API
 * shapes and the SDK, which is where a wrong payload hides.
 *
 * Usage: node scripts/smoke.mjs     (after `node scripts/build.mjs`)
 */
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)

const AT = '2026-11-24T03:04:05.000Z'
const settings = {
  gatewayUrl: 'https://gw.example',
  apiKey: 'k-1',
  systemId: 'sys-1',
  address: 'me@gw.example',
  intervalSeconds: 3600,
}

const mailPayload = {
  body: 'hello from the thread',
  message_id: '<m@x>',
  references: ['<root@x>'],
  subject: 'hi',
}

function makePi(scenario) {
  const calls = { fetch: [], append: [], import: [], ack: [], list: 0, toast: [] }
  let served = 0
  const pi = {
    plugin: {
      getId: () => 'agent-aimail.desktop',
      getSettings: async () => settings,
      getDataPath: async () => '/tmp',
    },
    services: { register: async (service) => { pi.__service = service } },
    session: {
      list: async () => {
        calls.list += 1
        return { items: scenario.sessions ?? [] }
      },
      appendMessage: async (input) => {
        calls.append.push(input)
        return { messageId: input.message?.id }
      },
      import: async (input) => {
        calls.import.push(input)
        return { sessionId: 's-new', imported: true, skipped: false }
      },
    },
    net: {
      fetch: async (input) => {
        calls.fetch.push(input.url)
        if (input.url.endsWith('/pending')) {
          served += 1
          if (served === 1) {
            return {
              status: 200,
              headers: { 'content-type': 'application/json' },
              bodyText: JSON.stringify({
                batches: [
                  {
                    body: JSON.stringify(mailPayload),
                    deliveries: [{ id: 42, email: 'me@gw.example', headers: { 'message-id': '<m@x>' } }],
                  },
                ],
              }),
            }
          }
          // An empty round: hold the loop quiet while the assertions run.
          await new Promise((r) => setTimeout(r, 60_000))
          return { status: 200, headers: {}, bodyText: '{"batches":[]}' }
        }
        if (input.url.endsWith('/ack')) {
          calls.ack.push(JSON.parse(input.body))
          return { status: 200, headers: {}, bodyText: '{"acked":1}' }
        }
        return { status: 404, headers: {}, bodyText: '' }
      },
    },
    ui: {
      showToast: async (message, level) => {
        calls.toast.push({ message, level })
      },
    },
  }
  return { pi, calls }
}

function failure(message) {
  console.error(`smoke: FAIL — ${message}`)
  process.exit(1)
}

async function waitFor(predicate, label, timeoutMs = 4000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await new Promise((r) => setTimeout(r, 20))
  }
  failure(`timed out waiting for ${label}`)
}

async function scenarioDelivers() {
  const { pi, calls } = makePi({ sessions: [] })
  globalThis.pi = pi
  delete require.cache[require.resolve(join(root, 'dist/main.js'))]
  const main = require(join(root, 'dist/main.js'))
  if (typeof main.onLoad !== 'function' || typeof main.onUnload !== 'function') {
    failure('the bundle must export onLoad and onUnload')
  }

  await main.onLoad()
  if (!pi.__service || pi.__service.id !== 'inbox') failure('onLoad must register the inbox service')

  await pi.__service.start({ log: () => {} })
  await waitFor(() => calls.ack.length > 0 || calls.toast.length > 0, 'the first pull round')

  if (calls.toast.length) failure(`service refused to start: ${JSON.stringify(calls.toast)}`)
  if (!calls.fetch.some((url) => url.endsWith('/api/v1/admin/pending'))) {
    failure('the pull must use the address-scoped pending path')
  }

  const created = calls.import[0]
  if (!created) failure('a new thread must allocate a session through session.import')
  if (created.title !== 'AIMail <root@x>') failure(`unexpected title: ${created.title}`)
  if (created.source !== 'aimail') failure(`unexpected source: ${created.source}`)
  if (created.externalId !== 'aimail-m-x') failure(`unexpected externalId: ${created.externalId}`)
  if (created.messages?.[0]?.content !== 'hello from the thread') {
    failure('the import must carry the mail body')
  }
  // An import that delivered the mail must not be followed by a second write.
  if (calls.append.length) failure('the mail was written twice (import + append)')

  const ack = calls.ack[0]
  if (!ack || ack.ids?.[0] !== 42) failure(`expected ack of delivery 42, got ${JSON.stringify(ack)}`)

  await main.onUnload()
  console.log('smoke: ok — pulled, imported one session for the thread, acked after delivery')
}

async function scenarioRefusalIsNotAcked() {
  const sessions = [{ sessionId: 's-1', title: 'AIMail <root@x>' }]
  const { pi, calls } = makePi({ sessions })
  // The host refuses every write into the existing session.
  pi.session.appendMessage = async (input) => {
    calls.append.push(input)
    return { error: { code: 'PERMISSION_DENIED' } }
  }
  globalThis.pi = pi
  delete require.cache[require.resolve(join(root, 'dist/main.js'))]
  const main = require(join(root, 'dist/main.js'))

  await main.onLoad()
  await pi.__service.start({ log: () => {} })
  await waitFor(() => calls.append.length > 0, 'the refused append')

  // Give the loop a moment to (wrongly) ack.
  await new Promise((r) => setTimeout(r, 120))
  if (calls.ack.length) failure(`a refused delivery must not be acked: ${JSON.stringify(calls.ack)}`)
  if (calls.append[0]?.sessionId !== 's-1') failure('the thread session must be reused')
  if (calls.append[0]?.message?.id !== 'aimail-m-x') failure('the message id must be deterministic')

  await main.onUnload()
  console.log('smoke: ok — a refused delivery stays pending (no ack)')
}

await scenarioDelivers()
await scenarioRefusalIsNotAcked()
