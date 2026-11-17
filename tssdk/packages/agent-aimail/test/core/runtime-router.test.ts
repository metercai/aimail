import { describe, expect, it } from 'vitest'
import { routeThread, threadRoot } from '../../src/runtime/session-router.js'

const ws = { id: 'w1', path: '/work/project' }

describe('host-neutral thread routing', () => {
  it('uses references[0], then in_reply_to, then message_id', () => {
    expect(threadRoot({ message_id: 'm', in_reply_to: 'r', references: ['ref', 'r'] })).toBe('ref')
    expect(threadRoot({ message_id: 'm', in_reply_to: 'r' })).toBe('r')
    expect(threadRoot({ message_id: 'm' })).toBe('m')
    expect(threadRoot({}, { 'in-reply-to': 'h' })).toBe('h')
  })

  it('follows up a known session in the same workspace', () => {
    expect(routeThread({ message_id: 'm', references: ['root'] }, ws, {
      session_id: 's1', workspace_path: '/work/project',
    })).toEqual({ kind: 'followup', sessionId: 's1', workspace: ws })
  })

  it('starts a new session when the thread has no mapping or the workspace moved', () => {
    expect(routeThread({ message_id: 'm' }, ws, undefined)).toEqual({ kind: 'new', workspace: ws })
    expect(routeThread({ message_id: 'm', references: ['root'] }, ws, {
      session_id: 's1', workspace_path: '/other',
    })).toEqual({ kind: 'new', workspace: ws })
  })
})
