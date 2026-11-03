/**
 * Board invite → creds: the board id is derived from the board address.
 *
 * B1 (2026-11-03): the .a2a@ address in the From header is the single board
 * handle. The 20-hex id is derived locally — sha256(lower(address))[:20],
 * byte-identical to the gateway's derive_board_id — so the `Board ID:` line
 * the gateway used to print is no longer sent or read, and board_creds.json
 * is keyed with the derived id (the gateway's storage layout).
 */
import { describe, it, expect } from 'vitest'
import { createHash } from 'node:crypto'
import { boardInviteFrom } from '../../src/core/board.js'

const sha20 = (s: string) => createHash('sha256').update(s, 'utf-8').digest('hex').slice(0, 20)

const BOARD_ADDR = 'brdpy.a2a@mail.example.test'
const BOARD_ID = sha20(BOARD_ADDR)

const BODY = `── A2A Board ──

Board Invitation
  Board: brdpy
  Board Email: brdpy.a2a@mail.example.test

── Information ──
API: http://127.0.0.1:8080
Token: bdt_0123456789abcdef`

const FROM = 'brdpy <brdpy.a2a@mail.example.test>'

describe('boardInviteFrom', () => {
  it('derives the board id from the address and keeps the token', () => {
    const inv = boardInviteFrom(BODY, FROM)
    expect(inv).not.toBeNull()
    expect(inv?.boardId).toBe(BOARD_ID)
    expect(inv?.token).toBe('bdt_0123456789abcdef')
    expect(inv?.gatewayUrl).toBe('http://127.0.0.1:8080')
    expect(inv?.boardEmail).toBe(BOARD_ADDR)
    // the derived id is a pure function of the address, never the gateway host
    expect(inv?.boardId).not.toBe(sha20('brdpy.a2a@127.0.0.1'))
  })

  it('ignores a stale `Board ID:` line from legacy invites', () => {
    const legacy = BODY.replace('Token: ', 'Board ID: bb74d6a76fe52bc0390e\nToken: ')
    const inv = boardInviteFrom(legacy, FROM)
    expect(inv?.boardId).toBe(BOARD_ID)
    expect(inv?.boardId).not.toBe('bb74d6a76fe52bc0390e')
  })

  it('has no invite without an API line', () => {
    expect(boardInviteFrom('Token: bdt_x', FROM)).toBeNull()
  })

  it('has no invite without a board address in the sender', () => {
    expect(boardInviteFrom(BODY, 'someone@example.test')).toBeNull()
  })
})
