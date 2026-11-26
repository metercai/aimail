/**
 * P0: DID 协议常量与向量镜像的对齐检查（TS 侧）。
 *
 * 与 `tests/sdk/test_did_constants.py` 对称，只保证三件事：
 *
 * 1. `contract/did-signature-vectors.json` 可解析、分组完整；
 * 2. `src/core/did-constants.ts` 的常量与向量 `constants` 一致
 *    （三语言共用字符串，不允许各写一份慢慢漂移）；
 * 3. 向量本身自洽：头里的 `bh`/`rh` 来自同源用例、签名对象七行末行带换行、
 *    载荷里不出现任何 hash。
 *
 * 字节层实现（`Content-v1` / 头 / Ed25519）由 did-sdk v0.3.0 提供，P4 接入，
 * 因此这里刻意**不**断言投影字节。
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { describe, it, expect } from 'vitest'

import {
  HEADER_NAME,
  RESULTS_HEADER_NAME,
  SYSTEM_MAIL_HEADER,
  PROTOCOL_VERSION,
  SIGNING_TAG,
  CONTENT_TAG,
  EMAIL_BINDING_TAG,
  DOMAIN_CONTROL_TAG,
  BODY_HASH_PREFIX,
  ANCHOR_HASH_PREFIX,
  ANCHOR_REF_PREFIX,
  ATTACHMENT_SEPARATOR,
  DID_ERRORS,
} from '../../src/core/did-constants.js'

const HERE = dirname(fileURLToPath(import.meta.url))
// test/core → test → agent-aimail → packages → tssdk → repo root
const REPO = resolve(HERE, '../../../../..')
const MIRROR = resolve(REPO, 'contract/did-signature-vectors.json')

const REQUIRED_GROUPS = [
  'constants',
  'content_v1',
  'rcpt_hash',
  'signing_input',
  'header',
  'payload_hash',
  'domain_control',
  'auth_results',
  'verdict',
  'payload',
] as const

interface Constants {
  header_name: string
  results_header_name: string
  system_mail_header: string
  protocol_version: string
  signing_tag: string
  content_tag: string
  email_binding_tag: string
  domain_control_tag: string
  body_hash_prefix: string
  anchor_hash_prefix: string
  anchor_ref_prefix: string
  attachment_separator: string
  errors: string[]
}

interface Vectors {
  constants: Constants
  content_v1: Array<{ case: string; body_hash: string }>
  rcpt_hash: Array<{ case: string; hex: string }>
  signing_input: Array<{ bytes_hex: string }>
  header: Array<{ fields: Record<string, string>; unfolded_len: number }>
  payload_hash: Array<{ hex: string }>
  domain_control: Array<{ hex: string }>
  payload: Array<{ expect_did_keys: string[]; expect_absent_keys: string[] }>
}

const vectors = JSON.parse(readFileSync(MIRROR, 'utf8')) as Vectors

describe('DID vectors mirror', () => {
  it('mirror exists at contract/did-signature-vectors.json', () => {
    expect(MIRROR.endsWith('contract/did-signature-vectors.json')).toBe(true)
  })

  it('has every group and none of them is empty', () => {
    for (const group of REQUIRED_GROUPS) {
      expect(vectors, `group ${group}`).toHaveProperty(group)
      if (group !== 'constants') {
        expect((vectors[group] as unknown[]).length).toBeGreaterThan(0)
      }
    }
  })
})

describe('DID constants', () => {
  it('match the vector file', () => {
    const c = vectors.constants
    expect(c.header_name).toBe(HEADER_NAME)
    expect(c.results_header_name).toBe(RESULTS_HEADER_NAME)
    expect(c.system_mail_header).toBe(SYSTEM_MAIL_HEADER)
    expect(c.protocol_version).toBe(PROTOCOL_VERSION)
    expect(c.signing_tag).toBe(SIGNING_TAG)
    expect(c.content_tag).toBe(CONTENT_TAG)
    expect(c.email_binding_tag).toBe(EMAIL_BINDING_TAG)
    expect(c.domain_control_tag).toBe(DOMAIN_CONTROL_TAG)
    expect(c.body_hash_prefix).toBe(BODY_HASH_PREFIX)
    expect(c.anchor_hash_prefix).toBe(ANCHOR_HASH_PREFIX)
    expect(c.anchor_ref_prefix).toBe(ANCHOR_REF_PREFIX)
    expect(c.attachment_separator).toBe(ATTACHMENT_SEPARATOR)
  })

  it('pins the same error vocabulary as the protocol', () => {
    // reason/错误码必须先钉死：三端都按这些字符串裁决。
    expect([...DID_ERRORS]).toEqual(vectors.constants.errors)
  })

  it('keeps the primitive split by layer', () => {
    expect(vectors.constants.body_hash_prefix).toBe('sha256:')
    expect(vectors.constants.anchor_hash_prefix).toBe('blake2b:')

    for (const c of vectors.content_v1) expect(c.body_hash.startsWith('sha256:')).toBe(true)
    for (const c of vectors.payload_hash) expect(c.hex.startsWith('blake2b:')).toBe(true)
    for (const c of vectors.domain_control) expect(c.hex.startsWith('blake2b:')).toBe(true)
  })
})

describe('DID vectors are self-consistent', () => {
  it('header echoes come from the same cases (never recomputed)', () => {
    const header = vectors.header[0].fields
    const rh = vectors.rcpt_hash.find((c) => c.case === 'header-main-set')
    expect(rh).toBeDefined()
    expect(header.rh).toBe(rh!.hex)
    expect(header.bh).toBe(vectors.content_v1[0].body_hash)
  })

  it('signing input is seven newline-terminated lines (N1)', () => {
    const raw = Buffer.from(vectors.signing_input[0].bytes_hex, 'hex')
    expect(raw.at(-1)).toBe(0x0a)
    expect(raw.filter((b) => b === 0x0a).length).toBe(7)
  })

  it('header unfolded length is pinned', () => {
    const { header, unfolded_len } = vectors.header[0]
    expect(header.length).toBe(unfolded_len)
    expect(unfolded_len).toBeLessThanOrEqual(998)
  })

  it('payload carries no hash (N9)', () => {
    const c = vectors.payload[0]
    expect(c.expect_did_keys).toContain('body_signed')
    expect(c.expect_did_keys).toContain('attachments')
    for (const bad of c.expect_absent_keys) {
      expect(c.expect_did_keys).not.toContain(bad)
    }
  })
})
