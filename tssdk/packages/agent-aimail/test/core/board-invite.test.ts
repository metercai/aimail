/**
 * Board invite → creds: the board id must be the gateway's.
 *
 * The invite states `Board ID:` (authoritative). The fallback mirrors the
 * gateway's derive_board_id (sha256 of the full board address [:20]) and the
 * address carries the MAIL domain of the sender — never the gateway host. The
 * old code derived the domain from the gateway URL, stored the token under an
 * id the gateway did not know, and every board call answered 401.
 */
import { describe, it, expect } from 'vitest'
import { createHash } from 'node:crypto'
import { boardInviteFrom } from '../../src/core/board.js'

const BODY = `── A2A Board ──

Board Invitation
  Board: brdpy
  Board Email: brdpy.a2a@mail.example.test

── Information ──
API: http://127.0.0.1:8080
Board ID: bb74d6a76fe52bc0390e
Token: bdt_0123456789abcdef`

const FROM = 'brdpy <brdpy.a2a@mail.example.test>'

describe('boardInviteFrom', () => {
  it('takes the stated board id and the token', () => {
    const inv = boardInviteFrom(BODY, FROM)
    expect(inv).not.toBeNull()
    expect(inv?.boardId).toBe('bb74d6a76fe52bc0390e')
    expect(inv?.token).toBe('bdt_0123456789abcdef')
    expect(inv?.gatewayUrl).toBe('http://127.0.0.1:8080')
    expect(inv?.boardEmail).toBe('brdpy.a2a@mail.example.test')
  })

  it('falls back to sha256(board address), never the gateway host', () => {
    const inv = boardInviteFrom(BODY.replace('Board ID: bb74d6a76fe52bc0390e\n', ''), FROM)
    const want = createHash('sha256').update('brdpy.a2a@mail.example.test', 'utf-8').digest('hex').slice(0, 20)
    expect(inv?.boardId).toBe(want)
    expect(inv?.boardId).not.toBe(
      createHash('sha256').update('brdpy.a2a@127.0.0.1', 'utf-8').digest('hex').slice(0, 20),
    )
  })

  it('has no invite without an API line', () => {
    expect(boardInviteFrom('Token: bdt_x', FROM)).toBeNull()
  })
})
